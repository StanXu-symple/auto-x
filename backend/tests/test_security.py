from app.core import security
from app.core.security import hash_password, verify_password


def test_password_hash_and_verify() -> None:
    digest = hash_password("correct horse battery staple")
    assert digest != "correct horse battery staple"
    assert verify_password("correct horse battery staple", digest)
    assert not verify_password("wrong", digest)


def test_backend_has_no_local_administrator_token_signer() -> None:
    assert not hasattr(security, "create_access_token")
