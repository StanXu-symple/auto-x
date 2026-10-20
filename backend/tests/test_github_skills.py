# ruff: noqa: F811
import io
import stat
import zipfile
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
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


def archive(contents):
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, data in contents.items():
            bundle.writestr("repo-commit/" + name, data)
    return result.getvalue()


def source_archive(body="Original writing rule", name="github-writer"):
    return archive(
        {
            "skills/writer/SKILL.md": (
                f"---\nname: {name}\ndescription: Writing\n---\n{body}\n"
                "[Guide](references/guide.md)"
            ),
            "skills/writer/references/guide.md": "Use concrete examples.",
            "skills/writer/scripts/tool.py": "raise RuntimeError('must never execute')",
            "skills/writer/assets/picture.png": b"\x89PNG\x00",
            "elsewhere/secret.txt": "outside selected directory",
        }
    )


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


def test_complete_files_references_and_immutable_checksum(package_root):
    prepared = packages.prepare_import("o/r", "main", "skills", "a" * 40, source_archive())
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


@pytest.mark.parametrize(
    "unsafe", ["../../outside.txt", "skills/../outside.txt", "skills/evil\\file"]
)
def test_archive_traversal_rejected(package_root, unsafe):
    with pytest.raises(APIError):
        packages.prepare_import("o/r", "main", "skills", "a" * 40, archive({unsafe: "x"}))
    assert not list(package_root.iterdir())


def test_symlinks_size_limits_and_case_collisions_rejected(package_root, monkeypatch):
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as bundle:
        link = zipfile.ZipInfo("repo/skills/SKILL.md")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        bundle.writestr(link, "/etc/passwd")
    with pytest.raises(APIError):
        packages.archive_files(result.getvalue(), "skills")
    with pytest.raises(APIError):
        packages.archive_files(archive({"skills/A.md": "a", "skills/a.md": "b"}), "skills")
    monkeypatch.setattr(packages, "MAX_TOTAL_BYTES", 4)
    with pytest.raises(APIError):
        packages.archive_files(source_archive(), "skills")


def test_prompt_limit_preserves_files_and_reports_omitted_reference(package_root, monkeypatch):
    monkeypatch.setattr(packages, "MAX_PROMPT_CHARS", 60)
    prepared = packages.prepare_import(
        "o/r",
        "main",
        "skills",
        "a" * 40,
        archive(
            {
                "skills/SKILL.md": "Write clearly. [ref](references/large.md)",
                "skills/references/large.md": "x" * 100,
            }
        ),
    )
    assert prepared.skills[0].metadata["warnings"]
    assert (prepared.directory / "repo/skills/references/large.md").read_text() == "x" * 100


async def test_download_pins_commit_and_never_follows_redirects(monkeypatch):
    original_client = httpx.AsyncClient
    requests = []

    async def handler(request):
        requests.append(str(request.url))
        if request.url.path.endswith("/commits/main"):
            return httpx.Response(200, json={"sha": "a" * 40})
        return httpx.Response(200, content=source_archive())

    monkeypatch.setattr(
        packages.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(**kwargs, transport=httpx.MockTransport(handler)),
    )
    result = await packages.download_source(
        GitHubSkillImport(url="https://github.com/o/r/tree/main/skills")
    )
    assert result[3] == "a" * 40
    assert requests == [
        "https://api.github.com/repos/o/r/commits/main",
        "https://codeload.github.com/o/r/zip/" + "a" * 40,
    ]


@pytest.fixture
def github_source(package_root, monkeypatch):
    download = AsyncMock(
        return_value=("owner/repository", "main", "skills", "a" * 40, source_archive())
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
    assert (await import_skill(cluster.api))["unchanged"] == 1
    assert len(list((package_root / "bundles").iterdir())) == 1
    assert (
        await cluster.api.patch(f"/api/v1/ai/skills/{skill_id}", json={"instructions": "edit"})
    ).status_code == 409
    selection = await cluster.client.context(
        monitored_user_id=1, feature_id=1, fallback_skill_ids=[], override_skill_ids=[skill_id, 1]
    )
    assert [s.id for s in selection.skills] == [skill_id, 1]
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
            skills=selection.skills,
            skill_resolution=selection.source,
            idempotency_key="github-job",
        )
        await db.commit()
        job_id, frozen = job.id, job.skill_snapshot
    github_source.return_value = (
        "owner/repository",
        "main",
        "skills",
        "b" * 40,
        source_archive("Updated writing rule"),
    )
    updated = await import_skill(cluster.api)
    assert updated["updated"] == 1
    assert updated["skills"][0]["id"] == skill_id
    assert updated["skills"][0]["version"] == 2
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


@pytest.mark.parametrize("ref,path", [("main", ""), ("release", "skills")])
async def test_same_commit_reimport_preserves_requested_scope_and_update_branch(
    cluster, github_source, package_root, ref, path
):
    original = (await import_skill(cluster.api))["skills"][0]
    github_source.return_value = (
        "owner/repository", ref, path, "a" * 40, source_archive()
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
        archive(
            {
                "skills/valid/SKILL.md": "Valid content",
                "skills/invalid/SKILL.md": "---\nname: [invalid]\n---\nContent",
            }
        ),
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
    github_source.assert_not_awaited()
    assert (await cluster.remote.get("/v1/jobs/1/skills")).status_code == 401
