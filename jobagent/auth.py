"""Sign-in: passwords, browser sessions, CSRF tokens and a login rate limit.

- Passwords: argon2id via pwdlib. Stored only as hashes.
- Sessions: a random token in an HttpOnly cookie; the database keeps its SHA-256 and a CSRF
  token. Every state-changing API call must repeat the CSRF token in the X-CSRF-Token header,
  which a cross-site form cannot do.
- Rate limit: a few failed logins per email or address, then a short lockout. In memory, per
  server process; enough for a small deployment.
"""
import hashlib
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from pwdlib import PasswordHash
from sqlalchemy import delete, select

from jobagent.db import session as db_session
from jobagent.models import Session, User

COOKIE = "jobagent_session"
SESSION_DAYS = int(os.environ.get("SESSION_DAYS", "30"))
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() in ("1", "true", "yes")  # set true behind HTTPS
REGISTRATION_OPEN = os.environ.get("REGISTRATION_OPEN", "true").lower() in ("1", "true", "yes")
MIN_PASSWORD = 10
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_hasher = PasswordHash.recommended()


class AuthError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# --- passwords ---------------------------------------------------------------------------------

def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    ok, _ = _hasher.verify_and_update(password, password_hash)
    return ok


def check_password_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"password must be at least {MIN_PASSWORD} characters")


def normalize_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not _EMAIL.match(email):
        raise AuthError("that does not look like an email address")
    return email


# --- rate limit --------------------------------------------------------------------------------

class RateLimiter:
    """`attempts` failures within `window` seconds lock a key for `lockout` seconds."""

    def __init__(self, attempts: int = 5, window: int = 300, lockout: int = 60):
        self.attempts, self.window, self.lockout = attempts, window, lockout
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.time()
        with self._lock:
            fails = [t for t in self._fails.get(key, []) if now - t < self.window]
            self._fails[key] = fails
            if len(fails) >= self.attempts and now - fails[-1] < self.lockout:
                raise AuthError("too many attempts; try again in a minute", 429)

    def failed(self, key: str) -> None:
        with self._lock:
            self._fails.setdefault(key, []).append(time.time())

    def clear(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


login_limiter = RateLimiter()


# --- users -------------------------------------------------------------------------------------

def register(email: str, password: str, name: str | None) -> User:
    if not REGISTRATION_OPEN:
        raise AuthError("registration is closed", 403)
    email = normalize_email(email)
    check_password_strength(password)
    with db_session() as s, s.begin():
        if s.scalar(select(User.id).where(User.email == email)):
            raise AuthError("an account with that email already exists", 409)
        user = User(email=email, name=(name or "").strip() or email.split("@")[0], password_hash=hash_password(password))
        s.add(user)
        s.flush()
        s.expunge(user)
    return user


def authenticate(email: str, password: str, client: str = "") -> User:
    email = normalize_email(email)
    for key in (f"e:{email}", f"ip:{client}"):
        login_limiter.check(key)
    with db_session() as s:
        user = s.scalar(select(User).where(User.email == email))
        if user is not None:
            s.expunge(user)
    if user is None or user.status != "active" or not verify_password(password, user.password_hash):
        login_limiter.failed(f"e:{email}")
        login_limiter.failed(f"ip:{client}")
        raise AuthError("wrong email or password", 401)
    login_limiter.clear(f"e:{email}")
    return user


def set_password(user_id: int, password: str, email: str | None = None) -> None:
    """Admin / CLI: set or reset a user's password (and optionally email) without the old one."""
    check_password_strength(password)
    with db_session() as s, s.begin():
        user = s.get(User, user_id)
        if user is None:
            raise AuthError("no such user", 404)
        user.password_hash = hash_password(password)
        if email:
            user.email = normalize_email(email)
        s.execute(delete(Session).where(Session.user_id == user_id))  # sign every browser out


def change_password(user_id: int, old: str, new: str, keep_token: str | None = None) -> None:
    with db_session() as s, s.begin():
        user = s.get(User, user_id)
        if not verify_password(old, user.password_hash):
            raise AuthError("the current password is wrong", 403)
        check_password_strength(new)
        user.password_hash = hash_password(new)
        q = delete(Session).where(Session.user_id == user_id)
        if keep_token:
            q = q.where(Session.token_hash != _hash(keep_token))
        s.execute(q)


# --- sessions ----------------------------------------------------------------------------------

def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: int, user_agent: str | None = None) -> tuple[str, str]:
    """Returns (cookie token, csrf token)."""
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    with db_session() as s, s.begin():
        s.add(Session(token_hash=_hash(token), user_id=user_id, csrf=csrf,
                      expires_at=datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS),
                      user_agent=(user_agent or "")[:300]))
    return token, csrf


def resolve_session(token: str | None) -> tuple[User, str] | None:
    """(user, csrf) for a valid cookie token, else None. Touches last_seen about once a minute."""
    if not token:
        return None
    now = datetime.now(timezone.utc)
    with db_session() as s, s.begin():
        row = s.get(Session, _hash(token))
        if row is None or row.expires_at < now:
            if row is not None:
                s.delete(row)
            return None
        user = s.get(User, row.user_id)
        if user is None or user.status != "active":
            return None
        if now - row.last_seen_at > timedelta(minutes=1):
            row.last_seen_at = now
        csrf = row.csrf
        s.expunge(user)
    return user, csrf


def destroy_session(token: str | None) -> None:
    if token:
        with db_session() as s, s.begin():
            s.execute(delete(Session).where(Session.token_hash == _hash(token)))


def purge_expired_sessions() -> int:
    with db_session() as s, s.begin():
        return s.execute(delete(Session).where(Session.expires_at < datetime.now(timezone.utc))).rowcount
