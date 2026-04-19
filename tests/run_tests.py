"""
Master test runner — runs all test modules.

Usage:
    python tests/run_tests.py
    python tests/run_tests.py --url sqlite:///:memory:
    python tests/run_tests.py --url postgresql://user:pass@host/db
    python tests/run_tests.py --url oracle+cx_oracle://user:pass@host/db
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.conftest import parse_url
import tests.test_common      as m_common
import tests.test_where_build as m_where
import tests.test_router      as m_router
import tests.test_tracker     as m_tracker
import tests.test_aligner     as m_aligner
import tests.test_pipeline    as m_pipeline
import tests.test_live_db     as m_livedb
import tests.test_harness_df_tosql as m_df_tosql


def main():
    url = parse_url()

    suites = [
        ("common",      lambda: m_common.run_all(url)),
        ("where_build", lambda: m_where.run_all()),
        ("router",      lambda: m_router.run_all(url)),
        ("tracker",     lambda: m_tracker.run_all(url)),
        ("aligner",     lambda: m_aligner.run_all()),
        ("pipeline",    lambda: m_pipeline.run_all(url)),
        ("live_db",     lambda: m_livedb.run_all(url)),
    ]

    total_passed = total_failed = 0

    for name, runner in suites:
        print(f"\n{'='*50}")
        print(f"  {name.upper()}  [{url}]")
        print(f"{'='*50}")
        try:
            p, f = runner()
        except Exception as e:
            print(f"  ERROR running suite '{name}': {e}")
            p, f = 0, 1
        total_passed += p
        total_failed += f

    print(f"\n{'='*50}")
    print(f"  TOTAL: {total_passed} passed, {total_failed} failed")
    print(f"{'='*50}")
    sys.exit(1 if total_failed else 0)


if __name__ == "__main__":
    main()
