"""
Schema fingerprinting and diff reporting.

Generates a stable hash of table schema metadata so that callers can detect
schema drift between ETL runs.  The fingerprint captures column names, types,
nullability, defaults, and identity/autoincrement flags.

Usage::

    from crud.fingerprint import build_fingerprint, diff_fingerprints

    fp_before = build_fingerprint(inspector, "my_table")
    # ... run DDL migration ...
    fp_after = build_fingerprint(inspector, "my_table")
    diff = diff_fingerprints(fp_before, fp_after)
    print(diff.summary())
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.engine.reflection import Inspector


# ---------------------------------------------------------------------------
# Column snapshot (one row of the fingerprint)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ColumnSnapshot:
    """Immutable snapshot of a single column's metadata."""

    name: str
    type_str: str
    nullable: bool
    default: Optional[str]
    autoincrement: bool
    identity: bool
    computed: bool

    def as_tuple(self) -> Tuple:
        return (
            self.name,
            self.type_str,
            self.nullable,
            self.default,
            self.autoincrement,
            self.identity,
            self.computed,
        )


# ---------------------------------------------------------------------------
# Full table fingerprint
# ---------------------------------------------------------------------------
@dataclass
class SchemaFingerprint:
    """Stable fingerprint of a table's schema at a point in time."""

    table: str
    schema: Optional[str]
    columns: List[ColumnSnapshot]
    hash: str = ""

    def __post_init__(self):
        if not self.hash:
            self.hash = self._compute_hash()

    def _compute_hash(self) -> str:
        """SHA-256 over sorted column tuples — deterministic regardless of column order."""
        payload = sorted(c.as_tuple() for c in self.columns)
        raw = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "table": self.table,
            "schema": self.schema,
            "hash": self.hash,
            "column_count": len(self.columns),
            "columns": [
                {
                    "name": c.name,
                    "type": c.type_str,
                    "nullable": c.nullable,
                    "default": c.default,
                    "autoincrement": c.autoincrement,
                    "identity": c.identity,
                    "computed": c.computed,
                }
                for c in self.columns
            ],
        }


# ---------------------------------------------------------------------------
# Schema diff
# ---------------------------------------------------------------------------
@dataclass
class SchemaDiff:
    """Human-readable diff between two fingerprints."""

    old_hash: str
    new_hash: str
    added: List[str] = field(default_factory=list)
    dropped: List[str] = field(default_factory=list)
    modified: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.old_hash != self.new_hash

    def summary(self) -> str:
        if not self.changed:
            return "Schema unchanged."
        parts = [f"Schema changed (hash {self.old_hash} → {self.new_hash})"]
        if self.added:
            parts.append(f"  Added columns: {self.added}")
        if self.dropped:
            parts.append(f"  Dropped columns: {self.dropped}")
        for m in self.modified:
            parts.append(f"  Modified '{m['column']}': {m['changes']}")
        return "\n".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "changed": self.changed,
            "old_hash": self.old_hash,
            "new_hash": self.new_hash,
            "added": self.added,
            "dropped": self.dropped,
            "modified": self.modified,
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def build_fingerprint(
    inspector: Inspector,
    table: str,
    schema: Optional[str] = None,
) -> SchemaFingerprint:
    """Build a fingerprint from live database metadata.

    Parameters
    ----------
    inspector : sqlalchemy Inspector
    table : str
    schema : str, optional
    """
    raw_cols = inspector.get_columns(table, schema=schema)
    snapshots: List[ColumnSnapshot] = []

    for col in raw_cols:
        snapshots.append(
            ColumnSnapshot(
                name=col["name"],
                type_str=str(col.get("type", "UNKNOWN")),
                nullable=col.get("nullable", True),
                default=str(col["default"]) if col.get("default") is not None else None,
                autoincrement=bool(col.get("autoincrement", False)),
                identity=bool(col.get("identity", False)),
                computed=bool(col.get("computed", False)),
            )
        )

    return SchemaFingerprint(table=table, schema=schema, columns=snapshots)


def diff_fingerprints(
    old: SchemaFingerprint,
    new: SchemaFingerprint,
) -> SchemaDiff:
    """Compare two fingerprints and return a human-readable diff.

    Parameters
    ----------
    old, new : SchemaFingerprint
    """
    old_map = {c.name: c for c in old.columns}
    new_map = {c.name: c for c in new.columns}

    added = [n for n in new_map if n not in old_map]
    dropped = [n for n in old_map if n not in new_map]

    modified: List[Dict[str, Any]] = []
    for name in old_map:
        if name in new_map:
            oc, nc = old_map[name], new_map[name]
            changes: Dict[str, Any] = {}
            if oc.type_str != nc.type_str:
                changes["type"] = f"{oc.type_str} → {nc.type_str}"
            if oc.nullable != nc.nullable:
                changes["nullable"] = f"{oc.nullable} → {nc.nullable}"
            if oc.default != nc.default:
                changes["default"] = f"{oc.default} → {nc.default}"
            if oc.autoincrement != nc.autoincrement:
                changes["autoincrement"] = f"{oc.autoincrement} → {nc.autoincrement}"
            if changes:
                modified.append({"column": name, "changes": changes})

    return SchemaDiff(
        old_hash=old.hash,
        new_hash=new.hash,
        added=added,
        dropped=dropped,
        modified=modified,
    )
