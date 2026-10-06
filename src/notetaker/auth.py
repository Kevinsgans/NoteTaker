import hashlib
import hmac
import json
import os
import secrets


def set_password(path, password):
    if len(password) < 12:
        raise ValueError("Use at least 12 characters")
    salt = secrets.token_bytes(32)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
    temp = path.with_suffix(".new")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"salt": salt.hex(), "digest": digest.hex()}, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def verify(path, password):
    data = json.loads(path.read_text())
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(data["salt"]), 600_000)
    return hmac.compare_digest(digest, bytes.fromhex(data["digest"]))
