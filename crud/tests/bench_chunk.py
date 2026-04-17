"""
Performance micro-benchmark for crud insert chunk sizes.

Generates synthetic DataFrames and times ``auto_insert`` with varying
chunk sizes against an in-memory SQLite database.

Usage::

    python -m crud.tests.bench_chunk
"""
from __future__ import annotations

import sys
import time
from typing import Dict, List

import numpy as np
import pandas as pd
import sqlalchemy as sa
from sqlalchemy import text

# Adjust path so ``crud`` is importable when run from the project root
sys.path.insert(0, ".")

from crud import auto_insert, CrudConfig


def _create_engine_with_table() -> sa.Engine:
    """Create fresh SQLite in-memory engine with a wide test table."""
    eng = sa.create_engine("sqlite:///:memory:")
    with eng.begin() as conn:
        conn.execute(text("""
            CREATE TABLE bench (
                id       INTEGER PRIMARY KEY,
                col_str  VARCHAR(100),
                col_int  INTEGER,
                col_flt  REAL,
                col_bool BOOLEAN,
                col_dt   DATETIME
            )
        """))
    return eng


def _make_df(n: int) -> pd.DataFrame:
    """Generate a synthetic DataFrame with *n* rows."""
    rng = np.random.default_rng(42)
    return pd.DataFrame({
        "id":       range(1, n + 1),
        "col_str":  [f"row_{i}" for i in range(n)],
        "col_int":  rng.integers(0, 100_000, size=n),
        "col_flt":  rng.random(n) * 1000,
        "col_bool": rng.choice([True, False], size=n),
        "col_dt":   pd.date_range("2024-01-01", periods=n, freq="s"),
    })


def benchmark(row_counts: List[int], chunk_sizes: List[int]) -> pd.DataFrame:
    """Run benchmarks and return a results table."""
    results: List[Dict] = []

    for n_rows in row_counts:
        df = _make_df(n_rows)
        for cs in chunk_sizes:
            eng = _create_engine_with_table()
            cfg = CrudConfig(
                chunk_size=cs,
                strict=False,
                add_missing_cols=False,
                enable_fingerprint=False,
            )
            t0 = time.perf_counter()
            res = auto_insert(eng, df, "bench", config=cfg)
            elapsed = time.perf_counter() - t0

            results.append({
                "rows": n_rows,
                "chunk_size": cs,
                "elapsed_s": round(elapsed, 3),
                "rows_per_sec": int(n_rows / elapsed) if elapsed > 0 else 0,
                "success": res.success,
                "method": res.method,
            })
            eng.dispose()
            print(f"  rows={n_rows:>7,}  chunk={cs:>6,}  elapsed={elapsed:.3f}s  ({results[-1]['rows_per_sec']:,} rows/s)")

    return pd.DataFrame(results)


def main():
    print("=" * 60)
    print("CRUD Insert Chunk-Size Benchmark")
    print("=" * 60)

    row_counts = [10_000, 50_000, 100_000]
    chunk_sizes = [500, 1_000, 5_000, 10_000]

    table = benchmark(row_counts, chunk_sizes)

    print("\n" + "=" * 60)
    print("Results Summary")
    print("=" * 60)
    pivot = table.pivot_table(
        index="rows",
        columns="chunk_size",
        values="rows_per_sec",
        aggfunc="first",
    )
    print(pivot.to_string())


if __name__ == "__main__":
    main()
