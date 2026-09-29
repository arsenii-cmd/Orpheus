"""Encryption of the "Личное" database at rest.

The key never touches the laptop's disk: the phone keeps it (Android Keystore) and hands it over
in "hello" on every connection; the server holds it only in memory. The database lives in RAM once
opened and is written back to disk encrypted after every change, as one AES-256-GCM blob:

    ORPHEUS-PERSONAL-1 | nonce (12 bytes) | ciphertext + tag

Without the key the file is unreadable; with a wrong key, opening fails loudly and nothing is
overwritten. The key is 32 random bytes, passed around as base64.
"""

import base64
import os
from pathlib import Path

MAGIC = b"ORPHEUS-PERSONAL-1"


class WrongKey(Exception):
    pass


def parse_key(text):
    """base64 -> 32 bytes; ValueError for anything else."""
    try:
        key = base64.b64decode(text.strip(), validate=True)
    except Exception:
        raise ValueError("ключ не в base64") from None
    if len(key) != 32:
        raise ValueError("ключ должен быть 32 байта, а не %d" % len(key))
    return key


def new_key():
    return base64.b64encode(os.urandom(32)).decode()


def seal(key: bytes, data: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(12)
    return MAGIC + nonce + AESGCM(key).encrypt(nonce, data, MAGIC)


def unseal(key: bytes, blob: bytes) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not blob.startswith(MAGIC):
        raise WrongKey("это не зашифрованная база Орфея")
    nonce = blob[len(MAGIC):len(MAGIC) + 12]
    try:
        return AESGCM(key).decrypt(nonce, blob[len(MAGIC) + 12:], MAGIC)
    except InvalidTag:
        raise WrongKey("ключ не подходит") from None


def write_atomic(path: Path, data: bytes):
    """A crash mid-write must never leave half a file: write aside, then rename over."""
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def shred(path: Path):
    """Overwrite a plaintext file before removing it (best effort: SSDs may keep old blocks)."""
    if not path.exists():
        return
    size = path.stat().st_size
    with open(path, "r+b") as f:
        f.write(os.urandom(size))
        f.flush()
        os.fsync(f.fileno())
    path.unlink()
