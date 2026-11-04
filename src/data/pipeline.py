"""One command to run the whole data pipeline:  python -m src.data.pipeline"""
from __future__ import annotations

import sys
from pathlib import Path

from src import config
from src.data.audit import run_audit, write_report
from src.data.build_db import build_database
from src.data.schema import SchemaError


def run(raw_dir: Path | None = None, db_path: Path | None = None) -> dict:
    raw_dir = Path(raw_dir or config.RAW_DIR)
    db_path = Path(db_path or config.DB_PATH)
    audit = run_audit(raw_dir)
    write_report(audit, config.DOCS_DIR / "DATA_QUALITY_REPORT.md", config.PROCESSED_DIR / "audit.json")
    stats = build_database(raw_dir, db_path, config.PROCESSED_DIR / "raw_manifest.json")
    return {"audit_issues": len(audit["issues"]), "db": stats}


if __name__ == "__main__":
    try:
        print(run())
    except SchemaError as exc:
        print(f"Schema validation failed: {exc}", file=sys.stderr)
        sys.exit(2)
