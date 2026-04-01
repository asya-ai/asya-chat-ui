import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

_PREFIX = "enc:v1:"


def _fernet() -> Fernet:
    key_material = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(key_material)
    return Fernet(key)


def encrypt_secret(value: str | None) -> str | None:
    if value is None:
        return None
    plain = value.strip()
    if not plain:
        return None
    if plain.startswith(_PREFIX):
        return plain
    token = _fernet().encrypt(plain.encode("utf-8")).decode("utf-8")
    return f"{_PREFIX}{token}"


def decrypt_secret(value: str | None) -> str | None:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    if not raw.startswith(_PREFIX):
        return raw
    payload = raw[len(_PREFIX) :]
    try:
        return _fernet().decrypt(payload.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        # Fail closed for invalid ciphertext.
        return None
