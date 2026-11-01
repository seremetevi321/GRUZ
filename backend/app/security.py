import hashlib
import secrets
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError

_ph = PasswordHasher()  # Argon2id, параметры по умолчанию библиотеки (RFC 9106)
# Хеш-пустышка: при неизвестном логине тратим то же время, что и при проверке пароля.
_DUMMY = _ph.hash("timing-equalizer")


def hash_password(p: str) -> str:
    return _ph.hash(p)


def verify_password(stored: str | None, p: str) -> bool:
    try:
        return _ph.verify(stored or _DUMMY, p) and stored is not None
    except (VerificationError, InvalidHashError):
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


class LoginLimiter:
    """Не более N попыток входа на логин за окно. Защита от перебора пароля."""

    def __init__(self, attempts: int = 10, window_s: int = 300):
        self.attempts, self.window = attempts, window_s
        self._hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.attempts:
            return False
        q.append(now)
        return True

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)


login_limiter = LoginLimiter()
