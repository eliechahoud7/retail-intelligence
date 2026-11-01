"""Exceptions raised by the ingestion layer."""

from __future__ import annotations


class IngestionError(Exception):
    """Base class for all ingestion failures."""


class MissingRawFilesError(IngestionError):
    """One or more expected raw files are absent."""

    def __init__(self, raw_dir, missing: list[str]):
        self.raw_dir = raw_dir
        self.missing = list(missing)
        super().__init__(f"{len(self.missing)} expected raw file(s) missing from {raw_dir}: "
                         + ", ".join(self.missing))


class SchemaMismatchError(IngestionError):
    """A raw file's header does not match the declared schema."""


class DatasetLoadError(IngestionError):
    """A raw file exists and has the right header but could not be parsed."""


class RawDataIntegrityError(IngestionError):
    """A raw file's checksum differs from the baseline, or changed during ingestion."""
