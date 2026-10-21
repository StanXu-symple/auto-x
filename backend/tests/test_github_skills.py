# ruff: noqa: F811
import asyncio
import io
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from git import Actor, Repo
from git.index import BaseIndexEntry
from gitdb import IStream
from sqlalchemy import select
from test_ai_listen_tasks import database  # noqa: F401
from test_skill_service import cluster  # noqa: F401

from app.ai_worker import AIGenerationWorker
from app.api.errors import APIError
from app.models.ai import AIGenerationJob, AISkill
from app.models.ai_data_source import AIDataSource
from app.models.tweet import Tweet
from app.schemas.skill_import import GitHubSkillImport
from app.services.ai_jobs import create_manual_job, get_ai_feature
from app.services.skill_client import SkillServiceUnavailable
from app.skills import packages


def source_files(body="Original writing rule", name="github-writer", path="skills"):
    files = {
        "skills/writer/SKILL.md": (
            f"---\nname: {name}\ndescription: Writing\n---\n{body}\n"
            "[Guide](references/guide.md)"
        ).encode(),
        "skills/writer/references/guide.md": b"Use concrete examples.",
        "skills/writer/scripts/tool.py": b"raise RuntimeError('must never execute')",
        "skills/writer/assets/picture.png": b"\x89PNG\x00",
        "elsewhere/secret.txt": b"outside selected directory",
    }
    return {name: value for name, value in files.items() if not path or name.startswith(path + "/")}


@pytest.fixture
def git_repository(tmp_path):
    repos = []

    def create(files=None):
        repo = Repo.init(tmp_path / ("origin-" + uuid.uuid4().hex), initial_branch="main")
        repos.append(repo)
        commit_files(repo, files or source_files(path=""))
        return repo

    yield create
    for repo in repos:
        repo.close()


def commit_files(repo, files):
    entries = []
    for path, content in files.items():
        mode, data = content if isinstance(content, tuple) else (0o100644, content)
        data = data.encode() if isinstance(data, str) else data
        blob = repo.odb.store(IStream("blob", len(data), io.BytesIO(data)))
        entries.append(BaseIndexEntry((mode, blob.binsha, 0, path)))
    repo.index.add(entries)
    actor = Actor("Skill test", "skill-test@example.test")
    return repo.index.commit("Update Skills", author=actor, committer=actor)


@pytest.fixture
def clone_origin(monkeypatch):
    original = packages._run_git
    calls = []

    def use(repo):
        def run(git, command, directory, deadline, cancelled, *args, **kwargs):
            calls.append((command, args, kwargs))
            if command == "clone":
                assert args[0] == "https://github.com/owner/repository.git"
                args = (Path(repo.working_dir).as_uri(), *args[1:])
            # The production importer allows HTTPS only. Permit a local fixture
            # transport here to exercise real Git without requiring the network.
            count = int(git.environment()["GIT_CONFIG_COUNT"])
            with git.custom_environment(**{
                "GIT_CONFIG_COUNT": str(count + 1),
                f"GIT_CONFIG_KEY_{count}": "protocol.file.allow",
                f"GIT_CONFIG_VALUE_{count}": "always",
            }):
                return original(git, command, directory, deadline, cancelled, *args, **kwargs)
        monkeypatch.setattr(packages, "_run_git", run)
        return calls

    return use


@pytest.fixture
def package_root(tmp_path, monkeypatch):
    monkeypatch.setattr(
        packages, "get_settings", lambda: SimpleNamespace(skill_storage_dir=str(tmp_path))
    )
    return tmp_path


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/o/r",
        "https://localhost/o/r",
        "https://github.com.evil.test/o/r",
        "https://user:pass@github.com/o/r",
        "https://github.com/o/r?token=secret",
        "https://github.com/o/r/tree/main/../../tmp",
        "https://github.com/o/r/blob/main/SKILL.md",
    ],
)
def test_import_rejects_unsupported_destinations_and_paths(url):
    with pytest.raises(APIError):
        packages.parse_source(GitHubSkillImport(url=url))


def test_source_supports_subdirectory_and_slash_branch():
    assert packages.parse_source(
        GitHubSkillImport(url="https://github.com/o/r/tree/main/xhs-Skills")
    ) == ("o/r", "main", "xhs-Skills")
    assert packages.parse_source(
        GitHubSkillImport(url="https://github.com/o/r", ref="feature/skills", path="xhs-Skills")
    ) == ("o/r", "feature/skills", "xhs-Skills")


@pytest.mark.parametrize("ref", ["--upload-pack=evil", "a..b", "main:other", "a\\b", "a b"])
def test_invalid_git_refs_rejected_before_clone(ref):
    with pytest.raises(APIError):
        packages.parse_source(GitHubSkillImport(url="https://github.com/o/r", ref=ref))


def test_complete_files_references_and_immutable_checksum(package_root):
    prepared = packages.prepare_import("o/r", "main", "skills", "a" * 40, source_files())
    skill = prepared.skills[0]
    assert "Original writing rule" in skill.instructions
    assert "Use concrete examples." in skill.instructions
    assert packages.read_package_instructions(skill.metadata) == skill.instructions
    assert (
        prepared.directory / "repo/skills/writer/assets/picture.png"
    ).read_bytes() == b"\x89PNG\x00"
    assert (prepared.directory / "repo/skills/writer/scripts/tool.py").is_file()
    assert not (prepared.directory / "repo/elsewhere").exists()
    assert skill.metadata["file_count"] == 4
    assert skill.metadata["reference_files"] == ["skills/writer/references/guide.md"]
    (prepared.directory / skill.metadata["prompt_file"]).write_text("tampered")
    with pytest.raises(APIError, match="校验失败"):
        packages.read_package_instructions(skill.metadata)


@pytest.mark.parametrize("unsafe", ["../outside.txt", "skills/evil\\file"])
def test_repository_paths_rejected(unsafe):
    with pytest.raises(APIError):
        packages.safe_relative(unsafe)


@pytest.mark.parametrize("files", [
    {"skills/SKILL.md": (0o120000, b"/etc/passwd")},
    {"skills/A.md": b"a", "skills/a.md": b"b"},
    {"skills/A/x.md": b"a", "skills/a/y.md": b"b"},
])
def test_symlinks_and_case_collisions_rejected(git_repository, files):
    repo = git_repository(files)
    with pytest.raises(APIError):
        packages.repository_files(repo, repo.head.commit.hexsha, "skills")


def test_selected_directory_and_size_limits(git_repository, monkeypatch):
    repo = git_repository()
    commit = repo.head.commit.hexsha
    assert packages.repository_files(repo, commit, "skills") == source_files()
    for path in ("missing", "skills/writer/SKILL.md"):
        with pytest.raises(APIError):
            packages.repository_files(repo, commit, path)
    monkeypatch.setattr(packages, "MAX_TOTAL_BYTES", 4)
    with pytest.raises(APIError):
        packages.repository_files(repo, commit, "skills")


def test_prompt_limit_preserves_files_and_reports_omitted_reference(package_root, monkeypatch):
    monkeypatch.setattr(packages, "MAX_PROMPT_CHARS", 60)
    prepared = packages.prepare_import(
        "o/r",
        "main",
        "skills",
        "a" * 40,
        {
            "skills/SKILL.md": b"Write clearly. [ref](references/large.md)",
            "skills/references/large.md": b"x" * 100,
        },
    )
    assert prepared.skills[0].metadata["warnings"]
    assert (prepared.directory / "repo/skills/references/large.md").read_text() == "x" * 100


@pytest.mark.parametrize("ref", [None, "main", "feature/skills", "v1", "commit"])
async def test_real_clone_default_branch_slash_branch_tag_and_commit(
    package_root, git_repository, clone_origin, ref,
):
    repo = git_repository()
    first = repo.head.commit.hexsha
    repo.create_tag("v1")
    repo.create_head("feature/skills")
    commit_files(repo, source_files("New rule"))
    requested = first if ref == "commit" else ref
    calls = clone_origin(repo)
    result = await packages.download_source(GitHubSkillImport(
        url="https://github.com/owner/repository", ref=requested, path="skills",
    ))
    expected = first if ref in {"v1", "feature/skills", "commit"} else repo.head.commit.hexsha
    assert result[:4] == ("owner/repository", requested or "main", "skills", expected)
    assert result[4] == source_files("Original writing rule" if expected == first else "New rule")
    assert calls[0][0] == "clone"
    assert calls[0][2]["depth"] == 1 and calls[0][2]["no_checkout"] is True
    assert [call[0] for call in calls] == (["clone", "fetch"] if ref == "commit" else ["clone"])
    assert not list(package_root.glob(".clone-*"))


async def test_clone_missing_ref_and_oversize_clean_temporary_files(
    package_root, git_repository, clone_origin, monkeypatch,
):
    clone_origin(git_repository())
    with pytest.raises(APIError, match="Git 拉取失败"):
        await packages.download_source(GitHubSkillImport(
            url="https://github.com/owner/repository", ref="missing",
        ))
    monkeypatch.setattr(packages, "MAX_CLONE_BYTES", 1)
    with pytest.raises(APIError, match="128 MiB"):
        await packages.download_source(GitHubSkillImport(url="https://github.com/owner/repository"))
    assert not list(package_root.glob(".clone-*"))


async def test_cancelled_download_waits_for_cleanup(package_root, monkeypatch):
    started, cleaned = threading.Event(), threading.Event()

    def clone(payload, cancelled):
        started.set()
        assert cancelled.wait(3)
        cleaned.set()
        raise packages.import_error("Cancelled")

    monkeypatch.setattr(packages, "_clone_source", clone)
    task = asyncio.create_task(packages.download_source(GitHubSkillImport(
        url="https://github.com/owner/repository",
    )))
    assert await asyncio.to_thread(started.wait, 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set()


@pytest.fixture
def github_source(package_root, monkeypatch):
    download = AsyncMock(
        return_value=("owner/repository", "main", "skills", "a" * 40, source_files())
    )
    monkeypatch.setattr(packages, "download_source", download)
    return download


async def import_skill(api):
    response = await api.post(
        "/api/v1/ai/skills/import-github",
        json={"url": "https://github.com/owner/repository/tree/main/skills"},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_http_import_mixed_selection_update_and_frozen_execution(
    cluster, github_source, package_root, monkeypatch
):
    imported = await import_skill(cluster.api)
    assert imported["created"] == 1
    github_skill = imported["skills"][0]
    skill_id = github_skill["id"]
    assert github_skill["source_type"] == "github"
    assert github_skill["content_type"] == "skill"
    assert (await import_skill(cluster.api))["unchanged"] == 1
    assert len(list((package_root / "bundles").iterdir())) == 1
    assert (
        await cluster.api.patch(f"/api/v1/ai/skills/{skill_id}", json={"instructions": "edit"})
    ).status_code == 409
    skills = await cluster.client.resolve([skill_id, 1])
    assert [s.id for s in skills] == [skill_id, 1]
    async with cluster.factory() as db:
        db.add(
            AIDataSource(
                id=1,
                base_url="https://example.test",
                model_name="test",
                encrypted_api_key="test",
                key_hint="test",
                key_fingerprint="test",
            )
        )
        tweet = Tweet(
            id=99,
            tweet_id="github-source",
            monitored_user_id=1,
            author_id="a",
            text="source facts",
            posted_at=datetime.now(UTC),
            raw_payload={},
        )
        db.add(tweet)
        await db.flush()
        job, _ = await create_manual_job(
            db,
            tweet=tweet,
            feature=await get_ai_feature(db, "article_generation"),
            skills=skills,
            skill_resolution="manual_override",
            idempotency_key="github-job",
        )
        await db.commit()
        job_id, frozen = job.id, job.skill_snapshot
    github_source.return_value = (
        "owner/repository",
        "main",
        "skills",
        "b" * 40,
        source_files("Updated writing rule"),
    )
    response = await cluster.api.post(f"/api/v1/ai/skills/{skill_id}/update-github")
    assert response.status_code == 200, response.text
    updated = response.json()
    assert github_source.call_args.args[0].model_dump() == {
        "url": "https://github.com/owner/repository", "ref": "main", "path": "skills",
    }
    assert updated["updated"] == 1
    assert updated["skills"][0]["id"] == skill_id
    assert updated["skills"][0]["version"] == 2
    assert updated["skills"][0]["content_type"] == "skill"
    assert len(list((package_root / "bundles").iterdir())) == 2
    assert "Original writing rule" in (await cluster.client.job(job_id, frozen))[0]["instructions"]
    monkeypatch.setattr("app.ai_worker.AsyncSessionFactory", cluster.factory)
    worker = object.__new__(AIGenerationWorker)
    worker.settings = SimpleNamespace(ai_worker_lock_ttl_seconds=60)
    worker.worker_id = "test-worker"
    claimed = await worker._claim(job_id, "claim")
    assert claimed.attempts == 1
    assert claimed.skill_snapshot == frozen
    assert "Updated writing rule" not in claimed.skill_snapshot[0]["instructions"]
    # Package loss is detected before another queued job consumes a model attempt.
    async with cluster.factory() as db:
        job = await db.get(AIGenerationJob, job_id)
        job.status = "queued"
        job.attempts = 0
        job.lifetime_attempts = 0
        await db.commit()
    old = (
        package_root
        / "bundles"
        / frozen[0]["source_metadata"]["bundle_id"]
        / frozen[0]["source_metadata"]["prompt_file"]
    )
    old.unlink()
    with pytest.raises(SkillServiceUnavailable):
        await worker._claim(job_id, "failed-claim")
    async with cluster.factory() as db:
        job = await db.get(AIGenerationJob, job_id)
        assert job.status == "queued" and job.attempts == 0


async def test_type_filters_and_mixed_task_selection_share_the_original_ids(
    cluster, github_source, package_root
):
    api = cluster.api
    imported = (await import_skill(api))["skills"][0]
    github_id = imported["id"]
    prompt_response = await api.post(
        "/api/v1/ai/skills",
        json={"name": "Extra prompt", "instructions": "Keep it concise", "content_type": "prompt"},
    )
    assert prompt_response.status_code == 201, prompt_response.text
    prompt_id = prompt_response.json()["id"]
    for page in (1, 2):
        response = await api.get(
            "/api/v1/ai/skills",
            params={"content_type": "prompt", "active": "true", "page_size": 1, "page": page},
        )
        assert response.status_code == 200
        assert response.json()["total"] == 2
        assert [item["content_type"] for item in response.json()["items"]] == ["prompt"]
    response = await api.get("/api/v1/ai/skills", params={"content_type": "skill"})
    assert response.json()["total"] == 1
    assert [item["id"] for item in response.json()["items"]] == [github_id]
    invalid = await api.get("/api/v1/ai/skills", params={"content_type": "invalid"})
    assert invalid.status_code == 422
    selection = [prompt_id, github_id, 1]
    assert [item.id for item in await cluster.client.resolve(selection)] == selection
    response = await api.post(
        "/api/v1/ai/listen-tasks",
        json={"name": "Mixed task", "desired_state": "paused",
              "monitored_user_ids": [1], "skill_ids": selection},
    )
    assert response.status_code == 201, response.text
    task = response.json()
    assert task["skill_ids"] == selection
    assert [item["content_type"] for item in task["skills"]] == ["prompt", "skill", "prompt"]
    assert (await cluster.client.task(task["id"])).selected_ids == selection
    response = await api.patch(
        f"/api/v1/ai/listen-tasks/{task['id']}",
        json={"config_version": task["config_version"], "skill_ids": [prompt_id]},
    )
    assert response.status_code == 200, response.text
    resolved = await cluster.client.task(task["id"])
    assert resolved.valid and resolved.selected_ids == [prompt_id]
    assert resolved.skills[0].instructions == "Keep it concise"


@pytest.mark.parametrize("ref,path", [("main", ""), ("release", "skills")])
async def test_same_commit_reimport_preserves_requested_scope_and_update_branch(
    cluster, github_source, package_root, ref, path
):
    original = (await import_skill(cluster.api))["skills"][0]
    github_source.return_value = (
        "owner/repository", ref, path, "a" * 40, source_files(path=path)
    )
    response = await cluster.api.post(
        "/api/v1/ai/skills/import-github",
        json={"url": "https://github.com/owner/repository", "ref": ref, "path": path},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["updated"] == 1
    skill = result["skills"][0]
    assert skill["id"] == original["id"]
    assert skill["version"] == original["version"] + 1
    assert skill["source_metadata"]["ref"] == ref
    assert skill["source_metadata"]["import_path"] == path
    directory = package_root / "bundles" / skill["source_metadata"]["bundle_id"]
    assert directory.is_dir()
    if not path:
        assert (directory / "repo/elsewhere/secret.txt").read_text() == "outside selected directory"
    assert len(list((package_root / "bundles").iterdir())) == 2


async def test_import_failure_rolls_back_all_rows_and_cleans_staging(
    cluster, github_source, package_root
):
    github_source.return_value = (
        "owner/repository",
        "main",
        "skills",
        "a" * 40,
        {
            "skills/valid/SKILL.md": b"Valid content",
            "skills/invalid/SKILL.md": b"---\nname: [invalid]\n---\nContent",
        },
    )
    response = await cluster.api.post(
        "/api/v1/ai/skills/import-github", json={"url": "https://github.com/owner/repository"}
    )
    assert response.status_code == 422
    async with cluster.factory() as db:
        assert list(await db.scalars(select(AISkill.id))) == [1]
    assert not list(package_root.iterdir())


async def test_import_and_resolve_require_existing_service_scopes(cluster, github_source):
    response = await cluster.remote.post(
        "/ai/skills/import-github",
        headers={
            "Authorization": f"Bearer {cluster.reader_token}",
            "X-User-Authorization": f"Bearer {cluster.admin_token}",
        },
        json={"url": "https://github.com/owner/repository"},
    )
    assert response.status_code == 401
    response = await cluster.remote.post(
        "/ai/skills/1/update-github",
        headers={
            "Authorization": f"Bearer {cluster.reader_token}",
            "X-User-Authorization": f"Bearer {cluster.admin_token}",
        },
    )
    assert response.status_code == 401
    github_source.assert_not_awaited()
    assert (await cluster.remote.get("/v1/jobs/1/skills")).status_code == 401


async def test_http_real_git_update_keeps_disabled_state_and_saved_scope(
    cluster, package_root, git_repository, clone_origin,
):
    repo = git_repository()
    clone_origin(repo)
    first = (await import_skill(cluster.api))["skills"][0]
    endpoint = f"/api/v1/ai/skills/{first['id']}/update-github"
    assert first["source_metadata"]["download_method"] == "git"
    response = await cluster.api.post(endpoint)
    assert response.status_code == 200, response.text
    assert response.json()["unchanged"] == 1
    assert len(list((package_root / "bundles").iterdir())) == 1
    response = await cluster.api.patch(
        f"/api/v1/ai/skills/{first['id']}", json={"is_active": False},
    )
    disabled_version = response.json()["version"]
    commit_files(repo, {
        **source_files("Latest writing rule"),
        "skills/new/SKILL.md": b"New Skill",
        "outside/SKILL.md": b"Do not import outside the original scope",
    })
    response = await cluster.api.post(endpoint)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["updated"] == 1 and result["created"] == 1
    current = next(skill for skill in result["skills"] if skill["id"] == first["id"])
    assert current["version"] == disabled_version + 1
    assert current["is_active"] is False
    assert "Latest writing rule" in current["instructions"]
    assert "Original writing rule" in packages.read_package_instructions(first["source_metadata"])
    assert len(list((package_root / "bundles").iterdir())) == 2
    repo.index.remove(["skills/writer/SKILL.md"])
    actor = Actor("Skill test", "skill-test@example.test")
    repo.index.commit("Remove selected skill", author=actor, committer=actor)
    response = await cluster.api.post(endpoint)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "skill_missing_upstream"
    assert (await cluster.api.get(f"/api/v1/ai/skills/{first['id']}")).json() == current
    assert len(list((package_root / "bundles").iterdir())) == 2
    assert not list(package_root.glob(".clone-*"))


async def test_update_legacy_zip_metadata_keeps_ids_and_old_package(
    cluster, github_source, package_root,
):
    first = (await import_skill(cluster.api))["skills"][0]
    async with cluster.factory() as db:
        skill = await db.get(AISkill, first["id"])
        metadata = dict(skill.source_metadata)
        metadata.pop("download_method")
        skill.source_metadata = metadata
        await db.commit()
    response = await cluster.api.post(f"/api/v1/ai/skills/{first['id']}/update-github")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["updated"] == 1
    assert result["skills"][0]["id"] == first["id"]
    assert result["skills"][0]["source_metadata"]["download_method"] == "git"
    assert packages.read_package_instructions(metadata) == first["instructions"]
    assert len(list((package_root / "bundles").iterdir())) == 2


async def test_update_rejects_custom_missing_or_busy_skills(cluster, github_source):
    from app.skills.routes import _IMPORT_LOCK

    assert (await cluster.api.post("/api/v1/ai/skills/1/update-github")).status_code == 409
    assert (await cluster.api.post("/api/v1/ai/skills/999/update-github")).status_code == 404
    async with _IMPORT_LOCK:
        response = await cluster.api.post("/api/v1/ai/skills/1/update-github")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "skill_import_busy"
    github_source.assert_not_awaited()


@pytest.mark.parametrize("reason", ["timeout", "cancelled", "size"])
def test_git_process_group_is_stopped_on_limits(tmp_path, reason):
    import os
    import subprocess
    import sys
    import time
    from unittest.mock import Mock

    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    cancelled = threading.Event()
    git = Mock()
    git.GIT_PYTHON_GIT_EXECUTABLE = "git"
    git.transform_kwargs.return_value = []

    def start(*args, **kwargs):
        if reason == "cancelled":
            cancelled.set()
        elif reason == "size":
            with (tmp_path / "large.pack").open("wb") as stream:
                stream.truncate(packages.MAX_CLONE_BYTES + 1)
        else:
            time.sleep(0.03)
        return process

    git.execute.side_effect = start
    deadline = time.monotonic() + (0.02 if reason == "timeout" else 10)
    try:
        with pytest.raises(APIError):
            packages._run_git(git, "clone", tmp_path, deadline, cancelled, "unused")
        assert process.poll() is not None
    finally:
        if process.poll() is None:
            os.killpg(process.pid, 9)
        process.communicate()


@pytest.mark.parametrize("path,expected_timeout", [
    ("/ai/skills/import-github", 180),
    ("/ai/skills/42/update-github", 180),
    ("/ai/skills/42", 10),
])
async def test_git_mutations_use_extended_service_timeout(monkeypatch, path, expected_timeout):
    import httpx

    from app.core.config import Settings
    from app.services.skill_client import SkillClient

    seen = []

    def handler(request):
        seen.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json={})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = Settings(_env_file=None, nacos_config_enabled=False, nacos_server_addr="")
    client = SkillClient(settings, http=http)
    client.tokens = SimpleNamespace(token=AsyncMock(return_value="test-token"))
    try:
        await client.request("POST", path)
        assert seen == [expected_timeout]
    finally:
        await http.aclose()
