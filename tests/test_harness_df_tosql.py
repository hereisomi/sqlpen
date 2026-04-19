import os
import unittest
import pandas as pd
from sqlalchemy import create_engine, text
from utils.harness import CrudTestHarness
from df_tosql import df_tosql

class TestHarnessDfToSql(unittest.TestCase):
    def setUp(self):
        # We will test in SQLite to keep it fast and isolated
        self.db_url = os.getenv("TEST_DB_URL", "sqlite:///test_df_tosql_harness.db")
        self.engine = create_engine(self.db_url)
        self.table_name = "test_crud_harness"
        
        # Create dummy df_src with enough rows to mathematically slice overlap in harness
        self.df_src = pd.DataFrame({
            "id": range(1, 41),
            "tenant": [f"T_{i%5}" for i in range(1, 41)],
            "value": [10.5 * i for i in range(1, 41)],
            "status": ["active"] * 40
        })
        self.pk_cols = ["id"]
        # tenant acts as a constraint in the update step
        self.constraint_cols = ["id"] 

        # Ensure clean state
        with self.engine.begin() as conn:
            conn.execute(text(f"DROP TABLE IF EXISTS {self.table_name}"))

        self.harness = CrudTestHarness(
            df_src=self.df_src,
            pk_cols=self.pk_cols,
            constraint_cols=self.constraint_cols,
            table_name=self.table_name
        )

    def test_df_tosql_crud_harness(self):
        # 1. INSERT phrase
        # It creates the table first and dumps insert_df
        res_insert = df_tosql(
            df=self.harness.insert_df,
            table=self.table_name,
            engine=self.engine,
            if_exist="insert",
            table_constraints={"pk": self.pk_cols},
            add_new_column=True
        )
        self.assertTrue(res_insert.get("table_created"))

        # validate after insert
        df_db = pd.read_sql_table(self.table_name, self.engine)
        self.harness.validate_after_insert(df_db)

        # 2. UPSERT phase
        # Updates overlapping rows, inserts new rows from upsert_df
        res_upsert = df_tosql(
            df=self.harness.upsert_df,
            table=self.table_name,
            engine=self.engine,
            if_exist="upsert",
            table_constraints={"pk": self.pk_cols},
            add_new_column=False
        )
        self.assertFalse(res_upsert.get("table_created"))
        
        df_db2 = pd.read_sql_table(self.table_name, self.engine)
        self.harness.validate_after_upsert(df_db2)

        # 3. UPDATE phase
        # Update specific rows using tenant as WHERE clause constraint, ignoring PK.
        # This will update all rows that share the same tenant.
        res_update = df_tosql(
            df=self.harness.update_df,
            table=self.table_name,
            engine=self.engine,
            if_exist="update",
            table_constraints={"pk": self.pk_cols},
            where=[f"{c} = ?" for c in self.constraint_cols], 
            add_new_column=False
        )
        
        # Validate full cycle
        df_db3 = pd.read_sql_table(self.table_name, self.engine)
        self.harness.validate_after_full_cycle(df_db3)


if __name__ == "__main__":
    unittest.main()
