import pytest

from app.api.errors import APIError
from app.api.routes.auth import change_password
from app.core.security import hash_password, verify_password
from app.models.admin import Admin
from app.schemas.auth import ChangePasswordRequest


class FakeDb:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


async def test_change_password_updates_the_persisted_hash() -> None:
    admin = Admin(username="admin", password_hash=hash_password("existing-password"))
    db = FakeDb()

    response = await change_password(
        ChangePasswordRequest(
            current_password="existing-password",
            new_password="new-secure-password",
        ),
        admin,
        db,  # type: ignore[arg-type]
    )

    assert response.message == "Password updated successfully"
    assert db.commits == 1
    assert verify_password("new-secure-password", admin.password_hash)
    assert not verify_password("existing-password", admin.password_hash)


@pytest.mark.parametrize(
    ("current_password", "new_password", "error_code"),
    [
        ("wrong-password", "new-secure-password", "current_password_invalid"),
        ("existing-password", "existing-password", "password_unchanged"),
    ],
)
async def test_change_password_rejects_invalid_updates(
    current_password: str,
    new_password: str,
    error_code: str,
) -> None:
    admin = Admin(username="admin", password_hash=hash_password("existing-password"))
    db = FakeDb()

    with pytest.raises(APIError) as exc_info:
        await change_password(
            ChangePasswordRequest(
                current_password=current_password,
                new_password=new_password,
            ),
            admin,
            db,  # type: ignore[arg-type]
        )

    assert exc_info.value.code == error_code
    assert db.commits == 0
    assert verify_password("existing-password", admin.password_hash)
