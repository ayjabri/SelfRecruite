"""Encryption for the few secrets users hand us (their own LLM API keys).

SECRET_KEY in .env is a Fernet key (`jobagent secret-key` prints a fresh one). Without it,
storing a key is refused rather than stored in the clear.
"""
import os

from cryptography.fernet import Fernet, InvalidToken


class NoSecretKey(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = os.environ.get("SECRET_KEY")
    if not key:
        raise NoSecretKey("SECRET_KEY is not set in .env; run `jobagent secret-key` and add the line it prints")
    return Fernet(key.encode())


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise NoSecretKey("the stored key cannot be decrypted with the current SECRET_KEY") from e


def new_key() -> str:
    return Fernet.generate_key().decode()
