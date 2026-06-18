"""Custom exception types for SpliceCraft (layer 0).

Extracted from the splicecraft.py monolith. Pure: depends on nothing but the
stdlib, imports nothing from the rest of the package, and is safe to import
from any layer. ``splicecraft.py`` re-exports both names, so
``import splicecraft as sc; sc.DataDirLockError`` keeps working unchanged.
"""
from __future__ import annotations


class DataDirLockError(RuntimeError):
    """Raised when the data-dir lock can't be acquired (another
    splicecraft is running). Caller is expected to surface the
    message verbatim."""


class _CliExit(Exception):
    """Raised by `_SubcommandParser` instead of calling sys.exit. The
    enclosing subcommand handler catches it and returns the carried
    integer code, preserving the historical `int`-returning shape of
    `_run_update_subcommand` / `_run_logs_subcommand`."""
    def __init__(self, code: int):
        super().__init__(f"_CliExit({code})")
        self.code = code
