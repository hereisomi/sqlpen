"""
Shared types, configuration, and result structures for the CRUD package.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence, Union

try:
    import pandas as pd
except ImportError:
    pd = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------
DataItem = Mapping[str, Any]
DataLike = Union[Sequence[DataItem], DataItem, "pd.DataFrame"]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass
class CrudConfig:
    """Central configuration for all CRUD operations."""

    chunk_size: int = 10_000
    tolerance: int = 5              # Max row failures before abort in fallback
    strict: bool = True             # Raise on validation failures vs log+continue
    add_missing_cols: bool = True   # ALTER TABLE ADD for extra df columns
    trace_sql: bool = False         # Dump generated SQL to file for debugging
    echo_sql: bool = False          # Output executed CRUD SQL statements to the console
    on_error: str = "coerce"        # "coerce" | "raise" — type coercion policy
    failure_threshold: float = 0.03 # Max fraction of coercion failures before abort
    byte_semantics: bool = False    # Use byte-length (not char-length) for VARCHAR truncation
    drop_extra_cols: bool = True    # True = silently drop; False = raise on extra columns
    enable_fingerprint: bool = True # Generate schema fingerprint on align()


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------
@dataclass
class CrudResult:
    """Uniform return type for all CRUD operations."""

    total: int = 0
    success: int = 0
    failed: int = 0
    method: str = "none"            # "bulk" | "row_fallback" | "none"
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    # ---- convenience helpers ------------------------------------------------
    def merge(self, other: "CrudResult") -> "CrudResult":
        """Merge chunk-level results into an aggregate."""
        return CrudResult(
            total=self.total + other.total,
            success=self.success + other.success,
            failed=self.failed + other.failed,
            method=other.method if self.method == "none" else self.method,
            diagnostics={
                **self.diagnostics,
                "chunks": self.diagnostics.get("chunks", [])
                + [other.diagnostics],
            },
        )
