import pytest
import pandas as pd
from unittest.mock import patch, MagicMock

from pipeline.oracle_monitor import (
    get_active_tables,
    find_candidate_time_cols,
    probe_freshness,
    run_oracle_audit
)

@pytest.fixture
def mock_engine():
    engine = MagicMock()
    engine.dialect.name = "oracle"
    
    # Mocking the connection for the MAX() fallback
    conn = MagicMock()
    conn.execute.return_value.scalar.return_value = "2026-04-17 10:00:00"
    engine.connect.return_value.__enter__.return_value = conn
    
    return engine

@patch("pipeline.oracle_monitor.pd.read_sql")
def test_get_active_tables(mock_read_sql, mock_engine):
    mock_df = pd.DataFrame({"table_name": ["PM_LTE_HOURLY", "CORE_SUBSCRIBER"]})
    mock_read_sql.return_value = mock_df
    
    df = get_active_tables(mock_engine, "PM_SCHEMA", 7)
    
    assert len(df) == 2
    assert "PM_LTE_HOURLY" in df["table_name"].values
    mock_read_sql.assert_called_once()

@patch("pipeline.oracle_monitor.pd.read_sql")
def test_find_candidate_time_cols(mock_read_sql, mock_engine):
    mock_df = pd.DataFrame({
        "table_name": ["PM_LTE_HOURLY"], 
        "column_name": ["PERIOD_START_TIME"],
        "data_type": ["TIMESTAMP"]
    })
    mock_read_sql.return_value = mock_df
    
    df = find_candidate_time_cols(mock_engine, "PM_SCHEMA")
    
    assert len(df) == 1
    assert "PERIOD_START_TIME" in df["column_name"].values

@patch("pipeline.oracle_monitor.get_latest_partition_value")
@patch("pipeline.oracle_monitor.time.sleep")
def test_probe_freshness_fallback(mock_sleep, mock_get_partition, mock_engine):
    # Setup conditions: No partition found, so it falls back to MAX()
    mock_get_partition.return_value = None
    
    target_df = pd.DataFrame({
        "table_name": ["PM_LTE_HOURLY"],
        "column_name": ["PERIOD_START_TIME"]
    })
    
    result_df = probe_freshness(mock_engine, "PM_SCHEMA", target_df, throttle_secs=0)
    
    assert len(result_df) == 1
    assert result_df.iloc[0]["last_update"] == "2026-04-17 10:00:00"
    mock_get_partition.assert_called_once()

@patch("pipeline.oracle_monitor.get_active_tables")
@patch("pipeline.oracle_monitor.find_candidate_time_cols")
@patch("pipeline.oracle_monitor.probe_freshness")
def test_run_oracle_audit_success(mock_probe, mock_find, mock_active, mock_engine):
    mock_active.return_value = pd.DataFrame({"table_name": ["T1"]})
    mock_find.return_value = pd.DataFrame({"table_name": ["T1"], "column_name": ["COL1"]})
    
    mock_probe.return_value = pd.DataFrame({
        "owner": ["SCHEMA"],
        "table_name": ["T1"], 
        "time_column": ["COL1"], 
        "last_update": ["2026-04-17"]
    })
    
    report = run_oracle_audit(mock_engine, "SCHEMA", 7)
    
    assert not report.empty
    assert report.iloc[0]["table_name"] == "T1"
    mock_probe.assert_called_once()

def test_run_oracle_audit_rejects_postgres():
    postgres_engine = MagicMock()
    postgres_engine.dialect.name = "postgresql"
    
    with pytest.raises(ValueError, match="explicitly requires an Oracle"):
        run_oracle_audit(postgres_engine, "public", 7)
