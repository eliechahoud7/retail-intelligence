"""Read-only integrity checks for raw files (SHA-256 checksums)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable, Mapping

from .errors import RawDataIntegrityError
from .schema import DatasetSpec

_CHUNK = 1 << 20  # 1 MiB


def sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest of a file, read in binary chunks (never written)."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checksum_snapshot(raw_dir: Path, specs: Iterable[DatasetSpec]) -> dict[str, str]:
    """Checksums of every expected raw file, keyed by dataset name."""
    return {spec.name: sha256_file(Path(raw_dir) / spec.filename) for spec in specs}


def verify_against_baseline(snapshot: Mapping[str, str], specs: Iterable[DatasetSpec]) -> None:
    """Raise if any raw file differs from its approved baseline checksum (strategy check V01)."""
    mismatches = [f"{s.filename}: expected {s.sha256}, found {snapshot[s.name]}"
                  for s in specs if snapshot[s.name] != s.sha256]
    if mismatches:
        raise RawDataIntegrityError(
            "Raw files differ from the approved baseline checksums:\n  " + "\n  ".join(mismatches))


def verify_unchanged(before: Mapping[str, str], after: Mapping[str, str]) -> None:
    """Raise if any raw file changed while ingestion was running."""
    changed = sorted(name for name in before if before[name] != after.get(name))
    if changed:
        raise RawDataIntegrityError(f"Raw files changed during ingestion: {', '.join(changed)}")
