"""Expected schemas for the five RavenStack CSVs, plus strict validation."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


class SchemaError(ValueError):
    """Raised when an input file does not match the expected structure."""


@dataclass(frozen=True)
class TableSpec:
    name: str
    filename: str
    pk: str
    columns: dict  # column -> DuckDB type used to parse it


TABLES: dict[str, TableSpec] = {
    "accounts": TableSpec("accounts", "ravenstack_accounts.csv", "account_id", {
        "account_id": "VARCHAR", "account_name": "VARCHAR", "industry": "VARCHAR", "country": "VARCHAR",
        "signup_date": "DATE", "referral_source": "VARCHAR", "plan_tier": "VARCHAR", "seats": "INTEGER",
        "is_trial": "BOOLEAN", "churn_flag": "BOOLEAN"}),
    "subscriptions": TableSpec("subscriptions", "ravenstack_subscriptions.csv", "subscription_id", {
        "subscription_id": "VARCHAR", "account_id": "VARCHAR", "start_date": "DATE", "end_date": "DATE",
        "plan_tier": "VARCHAR", "seats": "INTEGER", "mrr_amount": "DOUBLE", "arr_amount": "DOUBLE",
        "is_trial": "BOOLEAN", "upgrade_flag": "BOOLEAN", "downgrade_flag": "BOOLEAN", "churn_flag": "BOOLEAN",
        "billing_frequency": "VARCHAR", "auto_renew_flag": "BOOLEAN"}),
    "churn_events": TableSpec("churn_events", "ravenstack_churn_events.csv", "churn_event_id", {
        "churn_event_id": "VARCHAR", "account_id": "VARCHAR", "churn_date": "DATE", "reason_code": "VARCHAR",
        "refund_amount_usd": "DOUBLE", "preceding_upgrade_flag": "BOOLEAN", "preceding_downgrade_flag": "BOOLEAN",
        "is_reactivation": "BOOLEAN", "feedback_text": "VARCHAR"}),
    "support_tickets": TableSpec("support_tickets", "ravenstack_support_tickets.csv", "ticket_id", {
        "ticket_id": "VARCHAR", "account_id": "VARCHAR", "submitted_at": "TIMESTAMP", "closed_at": "TIMESTAMP",
        "resolution_time_hours": "DOUBLE", "priority": "VARCHAR", "first_response_time_minutes": "DOUBLE",
        "satisfaction_score": "DOUBLE", "escalation_flag": "BOOLEAN"}),
    "feature_usage": TableSpec("feature_usage", "ravenstack_feature_usage.csv", "usage_id", {
        "usage_id": "VARCHAR", "subscription_id": "VARCHAR", "usage_date": "DATE", "feature_name": "VARCHAR",
        "usage_count": "INTEGER", "usage_duration_secs": "DOUBLE", "error_count": "INTEGER",
        "is_beta_feature": "BOOLEAN"}),
}

FOREIGN_KEYS = [
    ("subscriptions", "account_id", "accounts", "account_id"),
    ("churn_events", "account_id", "accounts", "account_id"),
    ("support_tickets", "account_id", "accounts", "account_id"),
    ("feature_usage", "subscription_id", "subscriptions", "subscription_id"),
]

MAX_FILE_BYTES = 50 * 1024 * 1024  # defensive upper bound for any single input CSV


def validate_files(raw_dir: Path) -> dict[str, Path]:
    """Check every expected CSV exists, is reasonably sized, and has exactly the expected header."""
    raw_dir = Path(raw_dir)
    paths: dict[str, Path] = {}
    problems: list[str] = []
    for name, spec in TABLES.items():
        p = raw_dir / spec.filename
        if not p.exists():
            problems.append(f"missing file: {spec.filename}")
            continue
        if p.stat().st_size > MAX_FILE_BYTES:
            problems.append(f"{spec.filename}: file exceeds {MAX_FILE_BYTES // (1024 * 1024)} MB limit")
            continue
        try:
            header = list(pd.read_csv(p, nrows=0).columns)
        except Exception as exc:  # unreadable / not a CSV
            problems.append(f"{spec.filename}: cannot read header ({exc})")
            continue
        expected = list(spec.columns)
        missing = [c for c in expected if c not in header]
        extra = [c for c in header if c not in expected]
        if missing:
            problems.append(f"{spec.filename}: missing columns {missing}")
        if extra:
            problems.append(f"{spec.filename}: unexpected columns {extra}")
        paths[name] = p
    if problems:
        raise SchemaError("; ".join(problems))
    return paths
