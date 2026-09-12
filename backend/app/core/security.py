from pwdlib import PasswordHash

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, password_digest: str) -> bool:
    try:
        return password_hash.verify(password, password_digest)
    except (ValueError, TypeError):
        return False
