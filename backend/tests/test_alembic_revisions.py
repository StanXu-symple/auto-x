from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_revision_ids_fit_default_alembic_version_column() -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(backend_dir / "alembic.ini")
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    revisions = list(ScriptDirectory.from_config(config).walk_revisions())

    oversized = [revision.revision for revision in revisions if len(revision.revision) > 32]

    assert not oversized, f"Alembic revision IDs exceed VARCHAR(32): {oversized}"
