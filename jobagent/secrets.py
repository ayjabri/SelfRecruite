"""Encryption for the few secrets users hand us (their own LLM API keys).

SECRET_KEY in .env is a Fernet key (`jobagent secret-key` prints a fresh one). When it is not
set, a key is generated once and kept in data/secret.key (mode 600), so a fresh install works
without hand-editing; keys are never stored in the clear.
"""
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from jobagent.config import ROOT

KEY_FILE = ROOT / "data" / "secret.key"


class NoSecretKey(RuntimeError):
    pass


def _load_key() -> str:
    if key := os.environ.get("SECRET_KEY"):
        return key
    if KEY_FILE.exists():
        return KEY_FILE.read_text().strip()
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    key = new_key()
    KEY_FILE.touch(mode=0o600)
    KEY_FILE.write_text(key + "\n")
    return key


def _fernet() -> Fernet:
    try:
        return Fernet(_load_key().encode())
    except (ValueError, OSError) as e:
        raise NoSecretKey(f"SECRET_KEY is not a valid Fernet key and {KEY_FILE} could not be used: {e}") from e


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise NoSecretKey("the stored key cannot be decrypted with the current SECRET_KEY") from e


def new_key() -> str:
    return Fernet.generate_key().decode()
