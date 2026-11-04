"""Select data-quality issues relevant to a result so users see what could change the answer."""
from __future__ import annotations

import json
from functools import lru_cache

from src import config

_SEV = {"High": 0, "Medium": 1, "Low": 2, "Info": 3}


@lru_cache(maxsize=1)
def load_audit() -> dict:
    p = config.PROCESSED_DIR / "audit.json"
    if not p.exists():
        return {"issues": [], "passed_checks": [], "profile": [], "join_cardinality": {}}
    return json.loads(p.read_text())


def relevant_issues(tables=(), metric_ids=(), min_severity: str = "Medium", limit: int = 4) -> list[dict]:
    tables, metric_ids = set(tables), set(metric_ids)
    out = []
    for i in load_audit()["issues"]:
        if _SEV[i["severity"]] > _SEV[min_severity]:
            continue
        if tables & set(i.get("affects_tables", [])) or metric_ids & set(i.get("affects_metrics", [])):
            out.append(i)
    out.sort(key=lambda i: (_SEV[i["severity"]], -i["affected"]))
    return out[:limit]


def format_issue(i: dict) -> str:
    scale = f" ({i['affected']:,} of {i['total']:,} records, {i['pct']}%)" if i.get("total") else ""
    return f"**{i['id']} ({i['severity']}) - {i['title']}**{scale}: {i['impact']}"
