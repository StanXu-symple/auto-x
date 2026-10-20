"""Import public GitHub directories as immutable, non-executable Skill packages."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import posixpath
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlsplit

import yaml
from git import Git, GitError, Repo

from app.api.errors import APIError
from app.core.config import get_settings
from app.schemas.skill_import import GitHubSkillImport

MAX_CLONE_BYTES = 128 * 1024 * 1024
GIT_TIMEOUT_SECONDS = 120
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
    if ref and (
        len(ref) > 200
        or ref.startswith(("-", "/", "."))
        or ref.endswith(("/", ".", ".lock"))
        or any(c.isspace() or ord(c) < 32 or c in "~^:?*[\\" for c in ref)
        or any(part in ref for part in ("..", "//", "@{"))
    ):
        raise import_error("分支或版本无效")
    return repository, ref, safe_relative(path, allow_empty=True)


def git_environment() -> dict[str, str]:
    # Disable host configuration, credentials and executable hooks/filters.
    env = {}
    config = {
        "protocol.allow": "never",
        "protocol.https.allow": "always",
        "http.followRedirects": "false",
        "credential.helper": "",
        "core.hooksPath": os.devnull,
        "pack.threads": "1",
    }
    env.update(
        GIT_TERMINAL_PROMPT="0",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
        GIT_CONFIG_PARAMETERS="",
        GIT_ASKPASS="/bin/false",
        GIT_LFS_SKIP_SMUDGE="1",
        GIT_CONFIG_COUNT=str(len(config)),
    )
    for index, (key, value) in enumerate(config.items()):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    return env


def _check_clone(directory: Path, deadline: float, cancelled: threading.Event) -> None:
    if cancelled.is_set():
        raise import_error("Git 拉取已取消")
    if time.monotonic() >= deadline:
        raise import_error("Git 拉取超时，请稍后重试或选择较小的仓库")
    total = 0
    for parent, _, files in os.walk(directory):
        for name in files:
            try:
                total += (Path(parent) / name).lstat().st_size
            except FileNotFoundError:
                continue  # Git renames temporary packs while receiving objects.
            if total > MAX_CLONE_BYTES:
                raise import_error("Git 仓库超过 128 MiB，请使用较小的 Skill 仓库")


def _run_git(
    git: Git, command: str, directory: Path, deadline: float,
    cancelled: threading.Event, *args, **kwargs,
) -> None:
    # GitPython owns argument construction and process creation. A separate
    # process group lets cancellation/limits stop git-remote-https as well.
    _check_clone(directory, deadline, cancelled)
    process = git.execute(
        [git.GIT_PYTHON_GIT_EXECUTABLE, command, *git.transform_kwargs(**kwargs), *map(str, args)],
        as_process=True, start_new_session=True,
    )
    completed = False
    try:
        while True:
            _check_clone(directory, deadline, cancelled)
            try:
                process.communicate(timeout=0.2)
            except subprocess.TimeoutExpired:
                continue
            completed = True
            if process.returncode:
                raise import_error("Git 拉取失败，请检查公开仓库、分支或完整 Commit 及网络")
            _check_clone(directory, deadline, cancelled)
            return
    finally:
        if not completed:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()


def repository_files(repo: Repo, commit: str, selected_path: str) -> dict[str, bytes]:
    """Read tracked blobs without checking out files or invoking repository filters."""
    tree = repo.commit(commit).tree
    if selected_path:
        try:
            tree = tree / selected_path
        except KeyError as exc:
            raise import_error("指定目录不存在") from exc
    if tree.type != "tree":
        raise import_error("请选择仓库内的普通目录")
    files: dict[str, bytes] = {}
    seen: set[str] = set()
    total = 0
    for count, item in enumerate(tree.traverse(), start=1):
        if count > 50_000:
            raise import_error("仓库目录项过多")
        name = safe_relative(item.path)
        if any(part.casefold() == ".git" for part in name.split("/")):
            raise import_error("Skill 目录包含保留的 Git 路径")
        if name.casefold() in seen:
            raise import_error("Skill 目录包含重复或大小写冲突的文件")
        seen.add(name.casefold())
        if item.type == "tree":
            continue
        if stat.S_IFMT(item.mode) != stat.S_IFREG:
            raise import_error("Skill 目录包含符号链接、子模块或特殊文件，无法导入")
        total += item.size
        if len(files) >= MAX_FILES or item.size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise import_error("Skill 目录超过限制：2000 个文件、单文件 16 MiB、总计 96 MiB")
        files[name] = item.data_stream.read()
    if not files:
        raise import_error("指定目录不存在或没有文件")
    return files


def _clone_source(
    payload: GitHubSkillImport, cancelled: threading.Event,
) -> tuple[str, str, str, str, dict[str, bytes]]:
    repository, ref, path = parse_source(payload)
    root = Path(get_settings().skill_storage_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + GIT_TIMEOUT_SECONDS
    pinned_commit = bool(ref and re.fullmatch(r"[0-9a-fA-F]{40}", ref))
    try:
        with tempfile.TemporaryDirectory(prefix=".clone-", dir=root) as temporary:
            directory = Path(temporary) / "repo"
            git = Git()
            with git.custom_environment(**git_environment()):
                options = {"branch": ref} if ref and not pinned_commit else {}
                _run_git(
                    git, "clone", directory, deadline, cancelled,
                    f"https://github.com/{repository}.git", str(directory),
                    depth=1, single_branch=True, no_checkout=True, no_tags=True,
                    quiet=True, template="", **options,
                )
            with Repo(directory) as repo, repo.git.custom_environment(**git_environment()):
                if pinned_commit:
                    # Clone cannot use --branch with a commit SHA. Fetch the exact
                    # object explicitly; no branch checkout or history rewrite.
                    _run_git(
                        repo.git, "fetch", directory, deadline, cancelled,
                        "origin", ref, depth=1, no_tags=True, quiet=True,
                    )
                    commit = repo.commit("FETCH_HEAD").hexsha
                    if commit != ref.lower():
                        raise import_error("Git 返回的 Commit 与指定版本不一致")
                else:
                    commit = repo.head.commit.hexsha
                    ref = ref or repo.active_branch.name
                files = repository_files(repo, commit, path)
                _check_clone(directory, deadline, cancelled)
                return repository, ref, path, commit, files
    except (GitError, OSError, ValueError) as exc:
        raise import_error("无法通过 Git 拉取仓库，请检查 Git 安装、网络及仓库版本") from exc


async def download_source(
    payload: GitHubSkillImport,
) -> tuple[str, str, str, str, dict[str, bytes]]:
    cancelled = threading.Event()
    worker = asyncio.create_task(asyncio.to_thread(_clone_source, payload, cancelled))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        cancelled.set()
        # Keep the import lock until the Git process and temporary directory
        # have been cleaned up, even when the HTTP request is cancelled.
        try:
            await asyncio.shield(worker)
        except Exception:
            pass
        raise


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
    repository: str, ref: str, selected_path: str, commit: str, files: dict[str, bytes]
) -> PreparedImport:
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
                        "download_method": "git",
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
