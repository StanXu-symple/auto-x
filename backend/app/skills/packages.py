"""Import public GitHub directories as immutable, non-executable Skill packages."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import posixpath
import re
import shutil
import stat
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlsplit

import httpx
import yaml

from app.api.errors import APIError
from app.core.config import get_settings
from app.schemas.skill_import import GitHubSkillImport

MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_FILES = 2000
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 96 * 1024 * 1024
MAX_PROMPT_CHARS = 100_000
TEXT_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".csv"}


def import_error(message: str) -> APIError:
    return APIError(422, "github_skill_import_failed", message)


def safe_relative(value: str, *, allow_empty: bool = False) -> str:
    if not value and allow_empty:
        return ""
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or ":" in value
        or any(ord(c) < 32 for c in value)
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise import_error("路径必须是仓库内的相对目录，不能包含 .. 或特殊路径")
    return value


def parse_source(payload: GitHubSkillImport) -> tuple[str, str | None, str]:
    try:
        parsed = urlsplit(payload.url.strip().rstrip("/"))
    except ValueError as exc:
        raise import_error("GitHub 仓库地址无效") from exc
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != "github.com"
        or parsed.query
        or parsed.fragment
    ):
        raise import_error("请使用公开 github.com 仓库或目录的 HTTPS 地址")
    parts = unquote(parsed.path).strip("/").split("/")
    if len(parts) < 2 or not all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", p) for p in parts[:2]):
        raise import_error("GitHub 仓库地址无效")
    repository = f"{parts[0]}/{parts[1].removesuffix('.git')}"
    ref, path = payload.ref, payload.path or ""
    if len(parts) > 2:
        if len(parts) < 4 or parts[2] != "tree":
            raise import_error("请提供仓库地址或 tree 目录地址")
        ref = ref or parts[3]
        ref_parts = ref.split("/")
        if parts[3 : 3 + len(ref_parts)] != ref_parts:
            raise import_error("分支与目录地址不一致；可使用仓库根地址并单独填写分支和目录")
        path = payload.path if payload.path is not None else "/".join(parts[3 + len(ref_parts) :])
    if ref and (len(ref) > 200 or any(ord(c) < 32 for c in ref)):
        raise import_error("分支或版本无效")
    return repository, ref, safe_relative(path, allow_empty=True)


async def download_source(payload: GitHubSkillImport) -> tuple[str, str, str, str, bytes]:
    repository, ref, path = parse_source(payload)
    try:
        async with (
            asyncio.timeout(120),
            httpx.AsyncClient(
                timeout=30,
                trust_env=False,
                follow_redirects=False,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "Auto-X-Skill-Worker",
                },
            ) as client,
        ):
            if ref is None:
                response = await client.get(f"https://api.github.com/repos/{repository}")
                response.raise_for_status()
                ref = response.json()["default_branch"]
            response = await client.get(
                f"https://api.github.com/repos/{repository}/commits/{quote(ref, safe='')}"
            )
            response.raise_for_status()
            commit = response.json()["sha"]
            if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
                raise ValueError("invalid commit")
            chunks = bytearray()
            async with client.stream(
                "GET", f"https://codeload.github.com/{repository}/zip/{commit}"
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > MAX_ARCHIVE_BYTES:
                        raise import_error("仓库压缩包超过 32 MiB，请使用较小的 Skill 仓库")
            return repository, ref, path, commit, bytes(chunks)
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code in {403, 429}:
            raise APIError(429, "github_rate_limited", "GitHub 请求限流，请稍后重试") from exc
        raise import_error(f"GitHub 下载失败（HTTP {code}），请检查公开仓库、分支和目录") from exc
    except (httpx.HTTPError, TimeoutError, ValueError, KeyError) as exc:
        raise import_error("无法下载 GitHub 仓库，请检查网络或稍后重试") from exc


def archive_files(archive: bytes, selected_path: str) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    seen: set[str] = set()
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            entries = bundle.infolist()
            if len(entries) > 50_000:
                raise import_error("仓库文件过多")
            roots = {info.filename.split("/")[0] for info in entries}
            if len(roots) != 1:
                raise import_error("GitHub 压缩包目录格式无效")
            for info in entries:
                safe_relative(info.filename.rstrip("/"))
                name = info.filename.partition("/")[2].rstrip("/")
                if info.is_dir() or not name:
                    continue
                if selected_path and not name.startswith(selected_path + "/"):
                    continue
                mode = info.external_attr >> 16
                if stat.S_IFMT(mode) not in {0, stat.S_IFREG} or info.flag_bits & 1:
                    raise import_error("Skill 目录包含符号链接、特殊文件或加密文件，无法导入")
                if name.casefold() in seen:
                    raise import_error("Skill 目录包含重复或大小写冲突的文件")
                seen.add(name.casefold())
                total += info.file_size
                if (
                    len(files) >= MAX_FILES
                    or info.file_size > MAX_FILE_BYTES
                    or total > MAX_TOTAL_BYTES
                ):
                    raise import_error(
                        "Skill 目录超过限制：2000 个文件、单文件 16 MiB、总计 96 MiB"
                    )
                files[name] = bundle.read(info)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise import_error("GitHub 压缩包无效") from exc
    if not files:
        raise import_error("指定目录不存在或没有文件")
    return files


def parse_document(raw: bytes, fallback: str) -> tuple[str, str, str]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise import_error(f"{fallback}/SKILL.md 必须使用 UTF-8 编码") from exc
    metadata = {}
    body = text
    if text.startswith("---\n") or text.startswith("---\r\n"):
        match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.S)
        if not match or len(match[1]) > 16_000:
            raise import_error(f"{fallback}/SKILL.md 的元数据格式无效")
        try:
            metadata = yaml.safe_load(match[1]) or {}
        except yaml.YAMLError as exc:
            raise import_error(f"{fallback}/SKILL.md 的 YAML 元数据无效") from exc
        if not isinstance(metadata, dict):
            raise import_error("Skill 元数据必须包含 name / description 字段")
        body = text[match.end() :]
    name = metadata.get("name") or fallback
    description = metadata.get("description") or ""
    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name.strip()) > 100
        or not isinstance(description, str)
        or len(description) > 4000
    ):
        raise import_error(f"{fallback} 的名称或说明超出限制")
    if not body.strip() or len(text) > MAX_PROMPT_CHARS:
        raise import_error(f"{fallback}/SKILL.md 为空或超过 100000 字符")
    return name.strip(), description.strip(), body.strip()


def compile_prompt(
    entry: str, files: dict[str, bytes], body: str
) -> tuple[str, list[str], list[str]]:
    """Include local text references; programs and binaries are stored, never executed."""
    folder = posixpath.dirname(entry)
    prefix = f"{folder}/" if folder else ""
    pending = [
        p
        for p in sorted(files)
        if p.startswith(prefix + "references/")
        and PurePosixPath(p).suffix.lower() in TEXT_SUFFIXES
    ]
    warnings: list[str] = []
    included: list[str] = []
    visited = {entry}
    prompt = body

    def references(text: str, parent: str) -> list[str]:
        candidates = re.findall(r"\]\(([^)\s]+)(?:\s+[^)]*)?\)|`([^`\n]+)`", text)
        found = []
        for link, code in candidates:
            value = link or code
            try:
                parsed = urlsplit(value)
            except ValueError:
                continue
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            relative = unquote(parsed.path)
            if PurePosixPath(relative).suffix.lower() not in TEXT_SUFFIXES:
                continue
            resolved = posixpath.normpath(posixpath.join(parent, relative))
            if resolved in files and not resolved.endswith("/SKILL.md"):
                found.append(resolved)
            elif link and not resolved.startswith("../"):
                warnings.append(f"未载入引用文本：{value}（不在导入目录中）")
        return found

    pending = references(body, folder) + pending
    while pending:
        name = pending.pop(0)
        if name in visited:
            continue
        visited.add(name)
        try:
            content = files[name].decode("utf-8-sig")
        except UnicodeDecodeError:
            warnings.append(f"已保存但未载入非 UTF-8 参考文件：{name}")
            continue
        section = f"\n\n--- REFERENCE: {name} ---\n{content}"
        if len(prompt) + len(section) > MAX_PROMPT_CHARS:
            warnings.append(f"已保存但未载入超出上下文上限的参考文件：{name}")
            continue
        prompt += section
        included.append(name)
        pending.extend(references(content, posixpath.dirname(name)))
    return prompt, included, list(dict.fromkeys(warnings))


@dataclass
class PreparedSkill:
    key: str
    name: str
    description: str
    instructions: str
    metadata: dict


@dataclass
class PreparedImport:
    directory: Path
    repository: str
    commit: str
    skills: list[PreparedSkill]


def prepare_import(
    repository: str, ref: str, selected_path: str, commit: str, archive: bytes
) -> PreparedImport:
    files = archive_files(archive, selected_path)
    entries = sorted(p for p in files if PurePosixPath(p).name == "SKILL.md")
    if not entries or len(entries) > 50:
        raise import_error("导入目录必须包含 1–50 个 SKILL.md")
    root = Path(get_settings().skill_storage_dir).resolve()
    bundle_id = uuid.uuid4().hex
    directory = root / "bundles" / bundle_id
    staging = root / f".staging-{bundle_id}"
    skills = []
    try:
        for entry in entries:
            path = posixpath.dirname(entry)
            name, description, body = parse_document(
                files[entry], PurePosixPath(path).name or repository.split("/")[-1]
            )
            instructions, references, warnings = compile_prompt(entry, files, body)
            key = hashlib.sha256(f"{repository.lower()}:{path}".encode()).hexdigest()
            skills.append(
                PreparedSkill(
                    key,
                    name,
                    description,
                    instructions,
                    {
                        "repository": repository,
                        "import_path": selected_path,
                        "ref": ref,
                        "commit": commit,
                        "path": path,
                        "url": f"https://github.com/{repository}/tree/{quote(ref, safe='')}/{path}",
                        "bundle_id": bundle_id,
                        "prompt_file": f"prompts/{key}.md",
                        "sha256": hashlib.sha256(instructions.encode()).hexdigest(),
                        "entrypoint": f"repo/{entry}",
                        "reference_files": references,
                        "file_count": sum(1 for p in files if not path or p.startswith(path + "/")),
                        "warnings": warnings,
                        "mode": "writing_instructions",
                    },
                )
            )
        for name, data in files.items():
            target = staging / "repo" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o644)
        manifest = {
            name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in files.items()
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
        for skill in skills:
            target = staging / skill.metadata["prompt_file"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(skill.instructions, encoding="utf-8")
        directory.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(directory)
        return PreparedImport(directory, repository, commit, skills)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def read_package_instructions(metadata: dict) -> str:
    """Resolve an immutable snapshot, including versions no longer current in SQL."""
    try:
        bundle_id = metadata["bundle_id"]
        prompt_file = metadata["prompt_file"]
        digest = metadata["sha256"]
        if not re.fullmatch(r"[0-9a-f]{32}", bundle_id) or not re.fullmatch(
            r"prompts/[0-9a-f]{64}\.md", prompt_file
        ):
            raise ValueError("invalid package location")
        root = Path(get_settings().skill_storage_dir).resolve()
        path = root / "bundles" / bundle_id / prompt_file
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("invalid package path")
        with path.open("rb") as stream:
            raw = stream.read(MAX_PROMPT_CHARS * 4 + 1)
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("package content changed")
        return raw.decode("utf-8")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise APIError(
            503,
            "skill_package_unavailable",
            "Skill 文件缺失或校验失败，请检查持久化目录并恢复对应版本",
        ) from exc


def snapshot(skill) -> dict:
    result = {
        key: getattr(skill, key)
        for key in (
            "id",
            "name",
            "description",
            "instructions",
            "output_schema",
            "version",
            "remote_skill_id",
            "remote_skill_version",
        )
    }
    if getattr(skill, "source_type", "custom") == "github":
        result.update(source_type="github", source_metadata=skill.source_metadata)
    return result
