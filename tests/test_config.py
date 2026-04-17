"""
test_config.py — Tests for config.py

Covers:
- get_engine_from_env(): URL arg, env var, .env file, missing URL error
- ensure_table(): create, skip, fail, drop, alter
- load_pipeline_config(): defaults, yml overrides
- build_crud_config(): field mapping, percentage threshold conversion

Run against any database:
    pytest tests/test_config.py --url "postgresql://user:pass@localhost/mydb" -v
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest
import sqlalchemy as sa
from sqlalchemy import text

from config import get_engine_from_env, ensure_table, load_pipeline_config, build_crud_config
from crud.types import CrudConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _drop(engine, table):
    with engine.begin() as conn:
        conn.execute(text(f'DROP TABLE IF EXISTS "{table}"'))


def _sample_df(n=5):
    return pd.DataFrame({
        "id":   range(1, n + 1),
        "name": [f"User_{i}" for i in range(1, n + 1)],
        "val":  [float(i) for i in range(1, n + 1)],
    })


# ---------------------------------------------------------------------------
# get_engine_from_env
# ---------------------------------------------------------------------------

class TestGetEngineFromEnv:

    def test_explicit_url(self, db_url):
        engine = get_engine_from_env(db_url)
        assert engine is not None
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
        engine.dispose()

    def test_env_var(self, db_url, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", db_url)
        engine = get_engine_from_env()
        assert engine is not None
        engine.dispose()

    def test_dotenv_file(self, db_url, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text(f'DATABASE_URL={db_url}\n')
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        engine = get_engine_from_env()
        assert engine is not None
        engine.dispose()

    def test_missing_url_raises(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.chdir("/")
        with pytest.raises(ValueError, match="No database connection"):
            get_engine_from_env()


# ---------------------------------------------------------------------------
# ensure_table
# ---------------------------------------------------------------------------

class TestEnsureTable:

    def test_creates_new_table(self, engine):
        _drop(engine, "et_new")
        df = _sample_df()
        created = ensure_table(engine, df, "et_new", pk_cols=["id"])
        assert created is True
        assert sa.inspect(engine).has_table("et_new")

    def test_skip_existing_table(self, engine):
        _drop(engine, "et_skip")
        df = _sample_df()
        ensure_table(engine, df, "et_skip", pk_cols=["id"])
        created = ensure_table(engine, df, "et_skip", if_exists="skip")
        assert created is False

    def test_fail_on_existing_table(self, engine):
        _drop(engine, "et_fail")
        df = _sample_df()
        ensure_table(engine, df, "et_fail", pk_cols=["id"])
        with pytest.raises(ValueError, match="already exists"):
            ensure_table(engine, df, "et_fail", if_exists="fail")

    def test_drop_and_recreate(self, engine):
        _drop(engine, "et_drop")
        df = _sample_df()
        ensure_table(engine, df, "et_drop", pk_cols=["id"])
        created = ensure_table(engine, df, "et_drop",
                               pk_cols=["id"], if_exists="drop")
        assert created is True
        assert sa.inspect(engine).has_table("et_drop")

    def test_alter_adds_missing_column(self, engine):
        _drop(engine, "et_alter")
        df = _sample_df()
        ensure_table(engine, df, "et_alter", pk_cols=["id"])

        # Add a new column to the DataFrame
        df["new_col"] = "hello"
        ensure_table(engine, df, "et_alter", if_exists="alter")

        cols = {c["name"] for c in sa.inspect(engine).get_columns("et_alter")}
        assert "new_col" in cols

    def test_creates_with_pk(self, engine):
        _drop(engine, "et_pk")
        df = _sample_df()
        ensure_table(engine, df, "et_pk", pk_cols=["id"])
        pks = sa.inspect(engine).get_pk_constraint("et_pk")
        assert "id" in pks.get("constrained_columns", [])

    def test_creates_without_pk(self, engine):
        _drop(engine, "et_nopk")
        df = _sample_df()
        created = ensure_table(engine, df, "et_nopk")
        assert created is True
        assert sa.inspect(engine).has_table("et_nopk")


# ---------------------------------------------------------------------------
# load_pipeline_config
# ---------------------------------------------------------------------------

class TestLoadPipelineConfig:

    def test_returns_dict(self):
        cfg = load_pipeline_config()
        assert isinstance(cfg, dict)

    def test_default_keys_present(self):
        cfg = load_pipeline_config()
        assert "chunk_size" in cfg
        assert "casting" in cfg
        assert "cleaner" in cfg
        assert "outlier_pct" in cfg
        assert "on_error" in cfg
        assert "failure_threshold" in cfg

    def test_yml_overrides_defaults(self, tmp_path):
        yml = tmp_path / "config.yml"
        yml.write_text(
            "pipeline:\n  chunk_size: 9999\n  trace_sql: true\n"
            "schema_corrector:\n  add_missing_cols: true\n"
        )
        cfg = load_pipeline_config(path=yml)
        assert cfg["chunk_size"] == 9999
        assert cfg["trace_sql"] is True
        assert cfg["add_missing_cols"] is True

    def test_missing_yml_uses_defaults(self, tmp_path):
        cfg = load_pipeline_config(path=tmp_path / "nonexistent.yml")
        assert cfg["chunk_size"] == 10_000


# ---------------------------------------------------------------------------
# build_crud_config
# ---------------------------------------------------------------------------

class TestBuildCrudConfig:

    def test_returns_crud_config(self):
        cfg = build_crud_config()
        assert isinstance(cfg, CrudConfig)

    def test_overrides_applied(self):
        cfg = build_crud_config(overrides={"chunk_size": 500, "trace_sql": True})
        assert cfg.chunk_size == 500
        assert cfg.trace_sql is True

    def test_percentage_threshold_converted(self, tmp_path):
        yml = tmp_path / "config.yml"
        yml.write_text("schema_corrector:\n  failure_threshold: 5.0\n")
        cfg = build_crud_config(config_path=yml)
        # 5.0% → 0.05 fraction
        assert cfg.failure_threshold == pytest.approx(0.05)

    def test_fraction_threshold_unchanged(self, tmp_path):
        yml = tmp_path / "config.yml"
        yml.write_text("schema_corrector:\n  failure_threshold: 0.03\n")
        cfg = build_crud_config(config_path=yml)
        assert cfg.failure_threshold == pytest.approx(0.03)
