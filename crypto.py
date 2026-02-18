import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


def _derive_fernet_key(master_key: str) -> bytes:
    """
    Accepts either:
    - a urlsafe base64-encoded 32-byte fernet key (recommended), or
    - any passphrase-like string (we derive a stable key via SHA-256).
    """
    raw = master_key.strip().encode("utf-8")

    # Best case: user provided a valid Fernet key.
    try:
        decoded = base64.urlsafe_b64decode(raw)
        if len(decoded) == 32:
            # Fernet expects the *encoded* form as bytes.
            return raw
    except Exception:
        pass

    digest = hashlib.sha256(raw).digest()
    return base64.urlsafe_b64encode(digest)


def get_fernet(master_key: str) -> Fernet:
    return Fernet(_derive_fernet_key(master_key))


def encrypt_str(master_key: str, plaintext: str) -> str:
    f = get_fernet(master_key)
    token = f.encrypt(plaintext.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_str(master_key: str, token: str) -> str:
    f = get_fernet(master_key)
    try:
        pt = f.decrypt(token.encode("utf-8"))
    except InvalidToken as e:
        raise ValueError("Invalid master key or corrupted token") from e
    return pt.decode("utf-8")

