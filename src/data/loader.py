"""Typed, strict loading of raw CSVs into DuckDB views. Raw files are never modified."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb

from .schema import TABLES, SchemaError, validate_files


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def raw_manifest(raw_dir: Path) -> dict:
    """Checksums + sizes of raw files, written at build time so unchanged raw data can be proven later."""
    paths = validate_files(raw_dir)
    return {n: {"file": p.name, "bytes": p.stat().st_size, "sha256": sha256_of(p)} for n, p in paths.items()}


def register_raw_views(con: duckdb.DuckDBPyConnection, raw_dir: Path, prefix: str = "raw_") -> None:
    """Create strictly-typed views raw_<table> over the CSVs. Raises SchemaError on parse failures."""
    paths = validate_files(raw_dir)
    for name, spec in TABLES.items():
        types = ", ".join(f"'{c}': '{t}'" for c, t in spec.columns.items())
        path = str(paths[name]).replace("'", "''")
        sql = (f"CREATE OR REPLACE VIEW {prefix}{name} AS SELECT * FROM "
               f"read_csv('{path}', header=true, types={{{types}}}, ignore_errors=false)")
        con.execute(sql)
        try:
            # count(*) lets DuckDB skip column parsing, so materialise once to force every value to be type-checked now
            con.execute(f"CREATE OR REPLACE TEMP TABLE _typecheck AS SELECT * FROM {prefix}{name}")
            con.execute("DROP TABLE _typecheck")
        except duckdb.Error as exc:
            raise SchemaError(f"{spec.filename}: values do not match expected types ({exc})") from exc


def write_manifest(manifest: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2))
