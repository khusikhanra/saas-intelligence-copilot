"""Read-only database access with timeout and row limits. Also builds the DB on first run if missing."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import pandas as pd

from src import config
from src.security.sql_guard import ValidatedQuery, apply_row_limit, validate_sql


class QueryError(RuntimeError):
    """Execution failed; message is safe to show."""


class QueryTimeout(QueryError):
    pass


@dataclass
class QueryResult:
    df: pd.DataFrame
    sql: str
    truncated: bool = False
    elapsed_ms: int = 0
    warnings: list = field(default_factory=list)
    tables: set = field(default_factory=set)


def open_readonly(db_path: Path | None = None) -> duckdb.DuckDBPyConnection:
    db_path = Path(db_path or config.DB_PATH)
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found at {db_path}. Run: python -m src.data.pipeline")
    return duckdb.connect(str(db_path), read_only=True,
                          config={"enable_external_access": False, "memory_limit": config.DUCKDB_MEMORY_LIMIT, "threads": 2})


def _ensure_audit(raw_dir: Path) -> None:
    import json

    target = config.PROCESSED_DIR / "audit.json"
    if target.exists():
        return
    from src.data.audit import run_audit

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(run_audit(raw_dir), indent=2, default=str))


def ensure_database(db_path: Path | None = None) -> tuple[Path, str]:
    """Return (db_path, data_source_label). Builds the DB from data/raw, else from the bundled demo sample."""
    from src.data.build_db import build_database

    db_path = Path(db_path or config.DB_PATH)
    have_raw = (config.RAW_DIR / "ravenstack_accounts.csv").exists()
    have_sample = (config.SAMPLE_DIR / "ravenstack_accounts.csv").exists()
    src_dir, label = (config.RAW_DIR, "full dataset") if have_raw else (config.SAMPLE_DIR, "demo sample")
    if db_path.exists():
        if have_raw or have_sample:
            _ensure_audit(src_dir)
        return db_path, label
    if have_raw or have_sample:
        build_database(src_dir, db_path, config.PROCESSED_DIR / "raw_manifest.json" if have_raw else None)
        _ensure_audit(src_dir)
        return db_path, label
    raise FileNotFoundError("No data found. Place the five RavenStack CSVs in data/raw/ (see README) and run: python -m src.data.pipeline")


def run_query(con: duckdb.DuckDBPyConnection, sql: str, max_rows: int | None = None, timeout: float | None = None) -> QueryResult:
    """Validate then execute untrusted SQL: read-only, row-limited, time-limited."""
    max_rows = max_rows or config.QUERY_MAX_ROWS
    timeout = timeout or config.QUERY_TIMEOUT_SECONDS
    vq: ValidatedQuery = validate_sql(sql)
    final_sql = apply_row_limit(vq, max_rows)
    cur = con.cursor()
    timer = threading.Timer(timeout, cur.interrupt)
    t0 = time.perf_counter()
    timer.start()
    try:
        df = cur.execute(final_sql).fetchdf()
    except duckdb.InterruptException as exc:
        raise QueryTimeout(f"The query exceeded the {timeout:g}s time limit and was cancelled.") from exc
    except duckdb.Error as exc:
        raise QueryError("The query failed: " + str(exc).splitlines()[0][:200]) from exc
    finally:
        timer.cancel()
        cur.close()
    truncated = len(df) > max_rows
    if truncated:
        df = df.head(max_rows)
    return QueryResult(df=df, sql=vq.sql, truncated=truncated, elapsed_ms=int((time.perf_counter() - t0) * 1000),
                       warnings=list(vq.warnings), tables=set(vq.tables))
