import pytest

from jobagent.auth import AuthError, RateLimiter, check_password_strength, hash_password, normalize_email, verify_password


def test_password_hash_roundtrip():
    h = hash_password("correct horse battery")
    assert h.startswith("$argon2id$") and verify_password("correct horse battery", h)
    assert not verify_password("wrong", h) and not verify_password("x", None)


def test_strength_and_email():
    with pytest.raises(AuthError):
        check_password_strength("short")
    check_password_strength("long enough password")
    assert normalize_email("  Someone@Example.COM ") == "someone@example.com"
    with pytest.raises(AuthError):
        normalize_email("not an email")


def test_rate_limiter_locks_after_repeated_failures():
    rl = RateLimiter(attempts=3, window=60, lockout=60)
    for _ in range(3):
        rl.check("k"); rl.failed("k")
    with pytest.raises(AuthError) as e:
        rl.check("k")
    assert e.value.status == 429
    rl.clear("k")
    rl.check("k")
