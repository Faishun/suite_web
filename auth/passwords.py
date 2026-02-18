from passlib.context import CryptContext

# bcrypt is common, but it can break depending on the installed bcrypt/passlib versions
# (and bcrypt has a hard 72-byte password limit). For this internal UI, keep auth
# simple and robust by using PBKDF2-SHA256 (pure Python backend).
_PWD_CONTEXT = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def hash_password(password: str) -> str:
    return _PWD_CONTEXT.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _PWD_CONTEXT.verify(password, password_hash)

