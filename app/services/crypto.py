"""Symmetric encryption for secrets stored at rest in the DB.

Used for the CommCare password persisted in ``sync_config`` so that DB access
alone is not sufficient to leak field-survey credentials. The encryption key
lives in the host's ``.env`` as ``SYNC_ENCRYPTION_KEY`` (one 32-byte url-safe
base64 string, generated with ``Fernet.generate_key()`` ).

If the env var is missing the helpers raise — better to fail loudly at the
boundary than to silently store plaintext in a column named ``..._encrypted``.
"""
from __future__ import annotations

import os
from functools import lru_cache
from cryptography.fernet import Fernet, InvalidToken


class CryptoNotConfigured(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = os.environ.get("SYNC_ENCRYPTION_KEY")
    if not key:
        raise CryptoNotConfigured(
            "SYNC_ENCRYPTION_KEY is not set. Generate one with "
            "`python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"` "
            "and add it to your .env file."
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plaintext: str) -> str:
    """Encrypt a string. Returns url-safe base64 ciphertext."""
    if plaintext is None:
        return None
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt a previously-encrypted string. Raises on tamper or wrong key."""
    if ciphertext is None:
        return None
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as e:
        raise RuntimeError("Decryption failed — wrong key or tampered ciphertext") from e
