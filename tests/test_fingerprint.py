"""Fingerprint tests."""

from __future__ import annotations

from pathlib import Path

from media_insights.discovery.fingerprint import (
    fingerprint,
    fingerprint_changed,
    partial_fingerprint,
    stat_fingerprint,
)


def test_stat_fingerprint_changes_on_size(tmp_path: Path) -> None:
    p = tmp_path / "a.bin"
    p.write_bytes(b"hello")
    d1, _ = stat_fingerprint(p)
    p.write_bytes(b"hello world")
    d2, _ = stat_fingerprint(p)
    assert d1 != d2


def test_partial_fingerprint_changes_on_content(tmp_path: Path) -> None:
    p = tmp_path / "big.bin"
    p.write_bytes(b"X" * (1024 * 1024) + b"tail-a")
    d1 = partial_fingerprint(p, chunk=1024)
    p.write_bytes(b"X" * (1024 * 1024) + b"tail-b")
    d2 = partial_fingerprint(p, chunk=1024)
    assert d1 != d2


def test_partial_fingerprint_stable_across_renames(tmp_path: Path) -> None:
    p1 = tmp_path / "a.bin"
    p1.write_bytes(b"Y" * 4096 + b"end")
    p2 = tmp_path / "b.bin"
    p2.write_bytes(p1.read_bytes())
    assert partial_fingerprint(p1) == partial_fingerprint(p2)


def test_fingerprint_dispatch(tmp_path: Path) -> None:
    p = tmp_path / "x.bin"
    p.write_bytes(b"hello")
    digest, mtime = fingerprint(p, strategy="partial")
    assert digest and mtime > 0


def test_fingerprint_changed_after_rewrite(tmp_path: Path) -> None:
    p = tmp_path / "x.bin"
    p.write_bytes(b"hello world")
    d, m = fingerprint(p, "partial", 1024)
    p.write_bytes(b"goodbye world")
    assert fingerprint_changed(p, (d, m), "partial", 1024)
    # Stable on no rewrite
    d, m = fingerprint(p, "partial", 1024)
    assert not fingerprint_changed(p, (d, m), "partial", 1024)
