"""File fingerprinting: cheap ladder, content-aware."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


def stat_fingerprint(path: Path) -> tuple[str, float]:
    """Cheapest fingerprint: size + mtime. May miss same-size rewrites."""
    st = path.stat()
    digest = hashlib.blake2b(f"{st.st_size}:{int(st.st_mtime)}".encode(), digest_size=16).hexdigest()
    return digest, st.st_mtime


def _read_chunk(path: Path, offset: int, size: int) -> bytes:
    with open(path, "rb") as fh:
        fh.seek(offset)
        return fh.read(size)


def partial_fingerprint(path: Path, chunk: int = 8 * 1024 * 1024) -> str:
    """BLAKE2b over first/last chunk + size. Robust to renames, fast on remuxes."""
    st = path.stat()
    h = hashlib.blake2b(digest_size=32)
    h.update(str(st.st_size).encode())
    if st.st_size == 0:
        return h.hexdigest()
    head_size = min(chunk, st.st_size)
    h.update(_read_chunk(path, 0, head_size))
    if st.st_size > chunk * 2:
        h.update(_read_chunk(path, st.st_size - chunk, chunk))
    elif st.st_size > head_size:
        h.update(_read_chunk(path, head_size, st.st_size - head_size))
    return h.hexdigest()


def full_fingerprint(path: Path) -> str:
    h = hashlib.blake2b(digest_size=32)
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def fingerprint(path: Path, strategy: str = "partial", chunk: int = 8 * 1024 * 1024) -> tuple[str, float]:
    """Return (digest, mtime). Digest strategy follows the config."""
    st = path.stat()
    if strategy == "mtime":
        digest, _ = stat_fingerprint(path)
    elif strategy == "full":
        digest = full_fingerprint(path)
    else:
        digest = partial_fingerprint(path, chunk=chunk)
    return digest, st.st_mtime


def fingerprint_changed(
    path: Path, existing: tuple[str | None, float | None], strategy: str, chunk: int
) -> bool:
    """True if path's fingerprint differs from the stored one."""
    stored_digest, stored_mtime = existing
    if stored_digest is None or stored_mtime is None:
        return True
    if strategy == "mtime":
        new_digest, new_mtime = stat_fingerprint(path)
    else:
        new_digest, new_mtime = partial_fingerprint_with_size(path, strategy=strategy, chunk=chunk)
    return new_digest != stored_digest or new_mtime != stored_mtime


def partial_fingerprint_with_size(path: Path, strategy: str, chunk: int) -> tuple[str, float]:
    if strategy == "full":
        digest = full_fingerprint(path)
    else:
        digest = partial_fingerprint(path, chunk=chunk)
    return digest, path.stat().st_mtime


def fast_change_check(path: Path, stored_size: int | None, stored_mtime: float | None) -> bool:
    """Cheap pre-check used before doing any reads."""
    if stored_size is None or stored_mtime is None:
        return True
    st = path.stat()
    if st.st_size != stored_size:
        return True
    return abs(st.st_mtime - stored_mtime) > 1e-3


# Keep the helper around even if the cache isn't used yet; it documents intent.
def delete_if_missing(path: Path) -> bool:
    return not os.path.exists(path)
