"""Password hashing and bounded authentication attempts, with no paid providers."""
import hashlib
import secrets
import time
import threading
from fastapi import HTTPException


def hash_password(value):
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(value.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return salt+':'+digest


def verify(value, encoded):
    salt, digest = encoded.split(':')
    actual = hashlib.scrypt(value.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return secrets.compare_digest(actual, digest)


_attempts = {}
_lock = threading.Lock()

def throttle(request):
    key = request.client.host if request.client else 'unknown'
    now = time.monotonic()
    with _lock:
        for old in list(_attempts):
            _attempts[old] = [t for t in _attempts[old] if now-t < 300]
            if not _attempts[old]: del _attempts[old]
        attempts = _attempts.setdefault(key, [])
        if len(attempts) >= 15:
            raise HTTPException(429, 'Too many attempts. Try again in five minutes.')
        attempts.append(now)
