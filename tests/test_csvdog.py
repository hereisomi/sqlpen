"""
test_csvdog.py — Tests for pipeline.csvdog

Covers:
- New CSV files are loaded and manifest is created
- Unchanged files are skipped on re-run
- Modified files are re-processed
- Previously failed + unchanged files are skipped
- PK inference for new files (no manifest entry)
- Manifest persisted after each file
- Invalid directory raises ValueError

Run against any database:
    pytest tests/test_csvdog.py --url "postgresql://user:pass@localhost/mydb" -v
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import pytest
import sqlalchemy as sa
from sqlalchemy import text

from pipeline.csvdog import csvdog


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _drop(engine, table):
    with engine.begin() as conn:
        conn.execute(text(f'DROP TABLE IF EXISTS "{table}"'))


def _write_csv(path: Path, df: pd.DataFrame):
    df.to_csv(path, index=False)


def _make_df(n=10, id_offset=0):
    return pd.DataFrame({
        "id":    range(1 + id_offset, n + 1 + id_offset),
        "name":  [f"User_{i}" for i in range(1 + id_offset, n + 1 + id_offset)],
        "value": [float(i * 10) for i in range(1 + id_offset, n + 1 + id_offset)],
    })


# ---------------------------------------------------------------------------
# Basic loading
# ---------------------------------------------------------------------------

class TestCsvdogLoad:

    def test_new_csv_loaded(self, engine, tmp_path):
        _drop(engine, "users")
        csv = tmp_path / "users.csv"
        _write_csv(csv, _make_df())
        manifest = tmp_path / "manifest.json"

        results = csvdog(str(tmp_path), engine,
                         schema=str(manifest), chunk=1000, outlier=0)

        assert results["users"] == "success"
        assert manifest.exists()

    def test_manifest_records_mtime_and_pk(self, engine, tmp_path):
        _drop(engine, "items")
        csv = tmp_path / "items.csv"
        _write_csv(csv, _make_df())
        manifest = tmp_path / "manifest.json"

        csvdog(str(tmp_path), engine, schema=str(manifest))

        tracker = json.loads(manifest.read_text())
        assert "items" in tracker
        assert tracker["items"]["status"] == "success"
        assert tracker["items"]["mtime"] == csv.stat().st_mtime
        assert tracker["items"]["pk"] is not None

    def test_multiple_csvs_loaded(self, engine, tmp_path):
        for name in ("alpha", "beta", "gamma"):
            _drop(engine, name)
            _write_csv(tmp_path / f"{name}.csv", _make_df())

        manifest = tmp_path / "manifest.json"
        results = csvdog(str(tmp_path), engine, schema=str(manifest))

        assert all(v == "success" for v in results.values())
        assert len(results) == 3


# ---------------------------------------------------------------------------
# Skip logic
# ---------------------------------------------------------------------------

class TestCsvdogSkip:

    def test_unchanged_file_skipped(self, engine, tmp_path):
        _drop(engine, "products")
        csv = tmp_path / "products.csv"
        _write_csv(csv, _make_df())
        manifest = tmp_path / "manifest.json"

        # First run — loads
        csvdog(str(tmp_path), engine, schema=str(manifest))

        # Second run — same mtime → skip
        results = csvdog(str(tmp_path), engine, schema=str(manifest))
        assert results["products"] == "skipped"

    def test_modified_file_reprocessed(self, engine, tmp_path):
        _drop(engine, "orders")
        csv = tmp_path / "orders.csv"
        _write_csv(csv, _make_df())
        manifest = tmp_path / "manifest.json"

        csvdog(str(tmp_path), engine, schema=str(manifest))

        # Modify the file (new mtime)
        time.sleep(0.05)
        _write_csv(csv, _make_df(n=15))

        results = csvdog(str(tmp_path), engine, schema=str(manifest))
        assert results["orders"] == "success"

    def test_failed_unchanged_skipped(self, engine, tmp_path):
        """A file that previously failed and hasn't changed should be skipped."""
        csv = tmp_path / "broken.csv"
        _write_csv(csv, _make_df())
        manifest = tmp_path / "manifest.json"

        # Pre-seed manifest with a failed status at the current mtime
        tracker = {
            "broken": {
                "mtime": csv.stat().st_mtime,
                "pk": None,
                "status": "failed",
                "file": str(csv),
                "error": "simulated prior failure",
            }
        }
        manifest.write_text(json.dumps(tracker))

        results = csvdog(str(tmp_path), engine, schema=str(manifest))
        assert results["broken"] == "skipped"


# ---------------------------------------------------------------------------
# PK inference
# ---------------------------------------------------------------------------

class TestCsvdogPkInference:

    def test_pk_inferred_for_new_file(self, engine, tmp_path):
        _drop(engine, "events")
        csv = tmp_path / "events.csv"
        _write_csv(csv, _make_df())
        manifest = tmp_path / "manifest.json"

        csvdog(str(tmp_path), engine, schema=str(manifest))

        tracker = json.loads(manifest.read_text())
        assert tracker["events"]["pk"] is not None
        assert len(tracker["events"]["pk"]) > 0

    def test_known_pk_reused_on_second_run(self, engine, tmp_path):
        _drop(engine, "sessions")
        csv = tmp_path / "sessions.csv"
        _write_csv(csv, _make_df())
        manifest = tmp_path / "manifest.json"

        # First run — infers PK
        csvdog(str(tmp_path), engine, schema=str(manifest))
        tracker_after_first = json.loads(manifest.read_text())
        pk_first = tracker_after_first["sessions"]["pk"]

        # Modify file to trigger re-run
        time.sleep(0.05)
        _write_csv(csv, _make_df(n=12))

        csvdog(str(tmp_path), engine, schema=str(manifest))
        tracker_after_second = json.loads(manifest.read_text())
        pk_second = tracker_after_second["sessions"]["pk"]

        # PK should be preserved across runs
        assert pk_first == pk_second


# ---------------------------------------------------------------------------
# Manifest persistence
# ---------------------------------------------------------------------------

class TestManifestPersistence:

    def test_manifest_written_after_each_file(self, engine, tmp_path):
        """Manifest should exist and be valid JSON after processing."""
        for name in ("t1", "t2"):
            _drop(engine, name)
            _write_csv(tmp_path / f"{name}.csv", _make_df())

        manifest = tmp_path / "manifest.json"
        csvdog(str(tmp_path), engine, schema=str(manifest))

        tracker = json.loads(manifest.read_text())
        assert "t1" in tracker
        assert "t2" in tracker

    def test_corrupt_manifest_resets_gracefully(self, engine, tmp_path):
        _drop(engine, "recover")
        csv = tmp_path / "recover.csv"
        _write_csv(csv, _make_df())
        manifest = tmp_path / "manifest.json"

        # Write corrupt JSON
        manifest.write_text("{invalid json{{")

        # Should not raise — resets to empty tracker
        results = csvdog(str(tmp_path), engine, schema=str(manifest))
        assert results["recover"] == "success"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestCsvdogEdgeCases:

    def test_invalid_directory_raises(self, engine, tmp_path):
        with pytest.raises(ValueError, match="Directory not found"):
            csvdog(str(tmp_path / "nonexistent"), engine)

    def test_empty_directory_returns_empty(self, engine, tmp_path):
        results = csvdog(str(tmp_path), engine,
                         schema=str(tmp_path / "manifest.json"))
        assert results == {}
