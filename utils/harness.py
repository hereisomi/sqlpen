import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class QuerySpec:
    sql: str
    params: dict[str, Any]


class CrudTestHarness:
    _STR_MARKER = "⟐m"

    def __init__(self, df_src: pd.DataFrame, pk_cols: str | list[str], constraint_cols: str | list[str], table_name: str = "t"):
        self.df = self._validate_source_df(df_src)
        self.pk = self._as_cols(pk_cols, "pk_cols")
        self.constraint = self._as_cols(constraint_cols, "constraint_cols")
        self.table = self._validate_table_name(table_name)

        self._validate_key_columns()
        self.mutable = self._get_mutable_columns()

        self._validate_pk_uniqueness()

    # ----------------------------- configuration -----------------------------

    def _validate_source_df(self, df_src: pd.DataFrame) -> pd.DataFrame:
        if not isinstance(df_src, pd.DataFrame):
            raise ValueError("df_src must be a pandas DataFrame")
        if len(df_src) < 5:
            raise ValueError(f"df_src must have at least 5 rows to perform mathematical slicing, got {len(df_src)}")
        df_src = df_src.reset_index(drop=True)
        return df_src.copy()

    def _as_cols(self, cols: str | list[str], name: str) -> list[str]:
        if isinstance(cols, str):
            cols = [cols]
        if not isinstance(cols, list) or not cols:
            raise ValueError(f"{name} must be a non-empty string or list[str]")
        if not all(isinstance(c, str) and c for c in cols):
            raise ValueError(f"{name} must contain non-empty strings")
        return cols

    def _validate_table_name(self, table_name: str) -> str:
        if not isinstance(table_name, str) or not table_name:
            raise ValueError("table_name must be a non-empty string")
        return table_name

    def _validate_key_columns(self) -> None:
        cols = set(self.df.columns)
        pk_missing = [c for c in self.pk if c not in cols]
        constraint_missing = [c for c in self.constraint if c not in cols]

        if pk_missing:
            raise ValueError(f"Missing PK columns: {pk_missing}")
        if constraint_missing:
            raise ValueError(f"Missing constraint columns: {constraint_missing}")

        # overlap = sorted(set(self.pk) & set(self.constraint))
        # if overlap:
        #     raise ValueError(f"pk and constraint columns must be disjoint; overlap={overlap}")

    def _get_mutable_columns(self) -> list[str]:
        key_set = set(self.pk + self.constraint)
        mutable = [c for c in self.df.columns if c not in key_set]
        if not mutable:
            raise ValueError("No mutable columns found (all columns are keys)")
        return mutable

    def _validate_pk_uniqueness(self) -> None:
        # pandas 2.3+ removed the dropna kwarg from duplicated().
        # Fill nulls with a sentinel string to ensure null-safe duplicate detection.
        pk_df = self.df[self.pk].fillna("__NULL__")
        if pk_df.duplicated().any():
            raise ValueError("Source DataFrame contains duplicate PK values")

    # ----------------------------- test frames ------------------------------

    @property
    def insert_df(self) -> pd.DataFrame:
        # First 60% of data goes to INSERT
        idx = int(len(self.df) * 0.6)
        df = self.df.iloc[0:idx]
        return self._mutate_mutable(df)

    @property
    def upsert_df(self) -> pd.DataFrame:
        # UPSERT takes 30% from INSERT (overlapping) and 20% brand new data
        start_overlap = int(len(self.df) * 0.3)
        end_overlap   = int(len(self.df) * 0.6)
        end_new       = int(len(self.df) * 0.8)

        overlap_slice = self.df.iloc[start_overlap:end_overlap]
        new_slice     = self.df.iloc[end_overlap:end_new]

        existing = self._mutate_mutable(overlap_slice)
        new = new_slice.copy()
        return pd.concat([existing, new], axis=0)

    @property
    def update_df(self) -> pd.DataFrame:
        # Must select rows guaranteed to be in DB. 
        # Pick a few from the earliest overlapping range so they safely exist.
        idx_1 = 0
        idx_2 = int(len(self.df) * 0.4) 
        idx_3 = int(len(self.df) * 0.7) # Exists after upsert
        df = self.df.iloc[[idx_1, idx_2, idx_3]]
        return self._mutate_mutable(df)

    def _mutate_mutable(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        for col in self.mutable:
            out[col] = out[col].map(self._mutate_value, na_action="ignore")
        return out

    @classmethod
    def _mutate_value(cls, v: Any) -> Any:
        if pd.isna(v):
            return v
        if isinstance(v, (bool, np.bool_)):
            return not bool(v)
        if isinstance(v, (int, np.integer)) and not isinstance(v, (bool, np.bool_)):
            return int(v) + 1
        if isinstance(v, (float, np.floating)):
            return float(v) + 1.0
        if isinstance(v, pd.Timestamp):
            return v + pd.Timedelta(days=1)
        if isinstance(v, pd.Period):
            return v + 1
        if isinstance(v, str):
            return f"{v}{cls._STR_MARKER}"
        return v

    # ----------------------------- query builder ----------------------------

    def get_insert_check_query(self) -> QuerySpec:
        idx = int(len(self.df) * 0.6)
        spec = self._build_select_query(range(0, idx), self.pk, True, "ins")
        return spec

    def get_upsert_check_query(self) -> QuerySpec:
        start_overlap = int(len(self.df) * 0.3)
        end_new       = int(len(self.df) * 0.8)
        spec = self._build_select_query(range(start_overlap, end_new), self.pk, True, "ups")
        return spec

    def get_update_check_query(self) -> QuerySpec:
        idx_1 = 0
        idx_2 = int(len(self.df) * 0.4) 
        idx_3 = int(len(self.df) * 0.7)
        spec = self._build_select_query([idx_1, idx_2, idx_3], self.constraint, True, "upd")
        return spec

    def _build_select_query(self, indices: list[int] | range, key_cols: list[str], null_safe: bool, prefix: str) -> QuerySpec:
        table_sql = self._quote_ident(self.table)
        key_sql = {c: self._quote_ident(c) for c in key_cols}
        order_sql = ", ".join(key_sql[c] for c in key_cols)

        clauses: list[str] = []
        params: dict[str, Any] = {}

        for i, idx in enumerate(list(indices)):
            conds: list[str] = []
            for col in key_cols:
                val = self.df.at[idx, col]
                pname = f"{prefix}_{col}_{i}"
                cond = self._col_condition(key_sql[col], pname, val, null_safe)
                conds.append(cond)
                if cond.endswith(f":{pname}"):
                    # Always cast to native Python scalar so DB drivers don't drop params silently
                    params[pname] = val.item() if hasattr(val, "item") else val
            clauses.append(" AND ".join(conds))

        where = " OR ".join(f"({c})" for c in clauses)
        sql = f"SELECT * FROM {table_sql} WHERE {where} ORDER BY {order_sql}"
        return QuerySpec(sql=sql, params=params)

    def _col_condition(self, col_sql: str, pname: str, value: Any, null_safe: bool) -> str:
        if null_safe and pd.isna(value):
            return f"{col_sql} IS NULL"
        return f"{col_sql} = :{pname}"

    def _quote_ident(self, ident: str) -> str:
        if not isinstance(ident, str) or not ident:
            raise ValueError("Identifier must be a non-empty string")
        if "\x00" in ident:
            raise ValueError("Identifier contains NUL byte")
        escaped = ident.replace('"', '""')
        return f'"{escaped}"'

    # ------------------------------ comparison ------------------------------

    def validate_after_insert(self, db_df: pd.DataFrame) -> None:
        expected = self.insert_df
        self._assert_same_pk_set(db_df, expected)
        self._assert_frames_close(db_df, expected, "(after INSERT)")

    def validate_after_upsert(self, db_df: pd.DataFrame) -> None:
        expected = self._expected_after_upsert()
        self._assert_frames_close(db_df, expected, "(after UPSERT)")

    def validate_after_full_cycle(self, db_df: pd.DataFrame) -> None:
        expected = self._expected_after_full_cycle()
        self._assert_frames_close(db_df, expected, "(after full cycle INSERT→UPSERT→UPDATE)")

    def _expected_after_upsert(self) -> pd.DataFrame:
        base = self.insert_df.set_index(self.pk)
        changes = self.upsert_df.set_index(self.pk)

        overlap = changes.index.intersection(base.index)
        if len(overlap) == 0:
            raise AssertionError("UPSERT expected to overlap INSERT set but did not")

        base.update(changes[self.mutable])
        new_rows = changes[~changes.index.isin(base.index)]
        expected = pd.concat([base.reset_index(), new_rows.reset_index()], ignore_index=True)
        return expected[list(self.df.columns)]

    def _expected_after_full_cycle(self) -> pd.DataFrame:
        state = self._expected_after_upsert()
        state_idx = state.set_index(self.constraint)

        upd = self.update_df.set_index(self.constraint)
        missing = upd.index.difference(state_idx.index)
        if len(missing) > 0:
            raise AssertionError(f"UPDATE expected to match constraint keys but missing: {list(missing)[:10]}")

        state_idx.update(upd[self.mutable])
        expected = state_idx.reset_index()
        return expected[list(self.df.columns)]

    def _assert_same_pk_set(self, actual: pd.DataFrame, expected: pd.DataFrame) -> None:
        self._require_columns(actual, self.pk, "db_df")
        self._require_columns(expected, self.pk, "expected_df")

        act_keys = set(map(tuple, actual[self.pk].to_numpy()))
        exp_keys = set(map(tuple, expected[self.pk].to_numpy()))
        if act_keys != exp_keys:
            raise AssertionError(f"PK set mismatch\nExpected: {sorted(exp_keys)}\nActual:   {sorted(act_keys)}")

    def _require_columns(self, df: pd.DataFrame, cols: list[str], label: str) -> None:
        missing = [c for c in cols if c not in df.columns]
        if missing:
            raise AssertionError(f"{label} missing columns: {missing}")

    def _assert_frames_close(self, actual: pd.DataFrame, expected: pd.DataFrame, msg: str, rtol: float = 1e-5, atol: float = 1e-8, dt_tol: pd.Timedelta = pd.Timedelta("5s")) -> None:
        a = self._sort_for_compare(actual).reset_index(drop=True)
        b = self._sort_for_compare(expected).reset_index(drop=True)

        if list(a.columns) != list(b.columns):
            raise AssertionError(f"Column mismatch {msg}\nExpected: {list(b.columns)}\nActual:   {list(a.columns)}")
        if len(a) != len(b):
            raise AssertionError(f"Row count mismatch {msg}: expected {len(b)}, got {len(a)}")

        for col in a.columns:
            s1 = a[col]
            s2 = b[col]
            if self._is_datetime_like(s1) or self._is_datetime_like(s2):
                self._assert_datetime_close(s1, s2, dt_tol, col, msg)
                continue
            if self._is_numeric_like(s1) or self._is_numeric_like(s2):
                self._assert_numeric_close(s1, s2, rtol, atol, col, msg)
                continue
            self._assert_values_equal(s1, s2, col, msg)

    def _sort_for_compare(self, df: pd.DataFrame) -> pd.DataFrame:
        keys = [c for c in self.pk if c in df.columns]
        if not keys:
            keys = [c for c in self.constraint if c in df.columns]
        if not keys:
            keys = list(df.columns)
        return df.sort_values(keys, ignore_index=True)

    def _is_datetime_like(self, s: pd.Series) -> bool:
        ok = pd.api.types.is_datetime64_any_dtype(s) or isinstance(s.dtype, pd.DatetimeTZDtype)
        return bool(ok)

    def _is_numeric_like(self, s: pd.Series) -> bool:
        ok = pd.api.types.is_numeric_dtype(s)
        return bool(ok)

    def _assert_datetime_close(self, s1: pd.Series, s2: pd.Series, tol: pd.Timedelta, col: str, msg: str) -> None:
        d1 = pd.to_datetime(s1, errors="coerce")
        d2 = pd.to_datetime(s2, errors="coerce")

        both_na = d1.isna() & d2.isna()
        delta = (d1 - d2).abs()
        ok = both_na | (delta <= tol)

        if not bool(ok.all()):
            bad = (~ok).to_numpy().nonzero()[0][:5].tolist()
            raise AssertionError(f"Datetime difference too large in '{col}' {msg} (tol={tol}). Bad rows: {bad}")

    def _assert_numeric_close(self, s1: pd.Series, s2: pd.Series, rtol: float, atol: float, col: str, msg: str) -> None:
        x = pd.to_numeric(s1, errors="coerce").to_numpy(dtype=float)
        y = pd.to_numeric(s2, errors="coerce").to_numpy(dtype=float)

        if not np.allclose(x, y, rtol=rtol, atol=atol, equal_nan=True):
            raise AssertionError(f"Numeric values differ in '{col}' {msg} (rtol={rtol}, atol={atol})")

    def _assert_values_equal(self, s1: pd.Series, s2: pd.Series, col: str, msg: str) -> None:
        x = s1.to_numpy()
        y = s2.to_numpy()

        # np.array_equal with equal_nan=True crashes on string/object dtype in numpy >= 2.0
        if x.dtype.kind in ("U", "O") or y.dtype.kind in ("U", "O"):
            xs = pd.Series(x, dtype=object)
            ys = pd.Series(y, dtype=object)
            ok = bool((xs.eq(ys) | (xs.isna() & ys.isna())).all())
        else:
            try:
                ok = bool(np.array_equal(x, y, equal_nan=True))
            except TypeError:
                ok = bool(np.array_equal(x, y))

        if not ok:
            xs = pd.Series(x, dtype=object)
            ys = pd.Series(y, dtype=object)
            neq = ~(xs.eq(ys) | (xs.isna() & ys.isna()))
            bad = neq.to_numpy().nonzero()[0][:5].tolist()
            raise AssertionError(f"Values differ in '{col}' {msg}. Bad rows: {bad}")