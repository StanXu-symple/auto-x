from app.core.config import Settings
from app.models.xhs_credential import XiaohongshuCredential
from app.services.xhs_credentials import get_xhs_credentials, save_xhs_credentials


class FakeSession:
    def __init__(self) -> None:
        self.rows: dict[int, XiaohongshuCredential] = {}

    async def get(self, _: type[XiaohongshuCredential], admin_id: int):
        return self.rows.get(admin_id)

    def add(self, row: XiaohongshuCredential) -> None:
        self.rows[row.admin_id] = row

    async def commit(self) -> None:
        pass

    async def refresh(self, _: XiaohongshuCredential) -> None:
        pass


async def test_xhs_credentials_are_encrypted_and_scoped_to_admin() -> None:
    settings = Settings(_env_file=None, x_token_encryption_key="x" * 64)
    session = FakeSession()

    row = await save_xhs_credentials(
        session,  # type: ignore[arg-type]
        settings,
        admin_id=42,
        a1="a1-secret-value",
        web_session="web-session-secret-value",
    )

    assert row.admin_id == 42
    assert "a1-secret-value" not in row.encrypted_a1
    assert "web-session-secret-value" not in row.encrypted_web_session
    assert (
        await get_xhs_credentials(
            session,
            settings,
            admin_id=7,  # type: ignore[arg-type]
        )
        is None
    )

    restored = await get_xhs_credentials(
        session,
        settings,
        admin_id=42,  # type: ignore[arg-type]
    )
    assert restored is not None
    assert restored.a1 == "a1-secret-value"
    assert restored.web_session == "web-session-secret-value"
    assert restored.version == 1


async def test_updating_xhs_credentials_increments_version() -> None:
    settings = Settings(_env_file=None, x_token_encryption_key="x" * 64)
    session = FakeSession()
    await save_xhs_credentials(
        session,  # type: ignore[arg-type]
        settings,
        admin_id=42,
        a1="old-a1",
        web_session="old-session",
    )

    row = await save_xhs_credentials(
        session,  # type: ignore[arg-type]
        settings,
        admin_id=42,
        a1="new-a1",
        web_session="new-session",
    )

    assert row.version == 2
    restored = await get_xhs_credentials(
        session,
        settings,
        admin_id=42,  # type: ignore[arg-type]
    )
    assert restored is not None
    assert restored.a1 == "new-a1"
    assert restored.web_session == "new-session"
