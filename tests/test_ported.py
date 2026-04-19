import pytest
import pandas as pd
from sqlalchemy import create_engine, inspect, Column, Integer, String, MetaData, Table, text

from df_tosql import df_tosql
from utils.fingerprint import build_fingerprint, diff_fingerprints
from pipeline_runner import PipelineRunner

@pytest.fixture
def sqlite_engine():
    engine = create_engine("sqlite:///:memory:")
    yield engine
    engine.dispose()

def test_df_tosql_dict_input(sqlite_engine):
    data = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
    result = df_tosql(df=data, table="dict_test", engine=sqlite_engine, chunk=100)
    assert result.get("row_count") == 2

    # Verify data in db
    with sqlite_engine.connect() as conn:
        df_db = pd.read_sql("SELECT * FROM dict_test", conn)
        assert len(df_db) == 2
        assert df_db.iloc[0]["name"] == "Alice"

def test_fingerprint(sqlite_engine):
    metadata = MetaData()
    table = Table("fp_test", metadata,
                  Column("id", Integer, primary_key=True),
                  Column("name", String))
    metadata.create_all(sqlite_engine)

    inspector = inspect(sqlite_engine)
    fp1 = build_fingerprint(inspector, "fp_test")
    assert len(fp1.columns) == 2

    # Alter table (use begin to commit DDL)
    with sqlite_engine.begin() as conn:
        conn.execute(text("ALTER TABLE fp_test ADD COLUMN age INTEGER"))
    
    inspector2 = inspect(sqlite_engine)
    fp2 = build_fingerprint(inspector2, "fp_test")
    assert len(fp2.columns) == 3

    diff = diff_fingerprints(fp1, fp2)
    assert diff.changed
    assert "age" in diff.added

def test_pipeline_runner(sqlite_engine):
    df = pd.DataFrame({
        "id": range(10),
        "email": [f"user{i}@test.com" for i in range(10)],
        "score": [100] * 10
    })

    runner = PipelineRunner(
        engine=sqlite_engine,
        source_df=df,
        table="pipeline_test",
        pk_cols=["id"],
        constraint_cols=["email"],
    )
    report = runner.run()

    assert report.all_passed
    assert len(report.steps) == 3
    assert report.steps[0].step == "INSERT"
    assert report.steps[1].step == "UPSERT"
    assert report.steps[2].step == "UPDATE"
