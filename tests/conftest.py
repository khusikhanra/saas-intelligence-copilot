"""Shared fixtures. `tiny` is a hand-built dataset with answers derived by hand (see comments)."""
from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd
import pytest

from src.analytics.db import open_readonly
from src.data.build_db import build_database

ACCOUNTS = [  # id, name, industry, country, signup, source, plan, seats, is_trial, churn_flag
    ("A1", "Co1", "DevTools", "US", "2024-01-10", "organic", "Basic", 2, False, False),
    ("A2", "Co2", "FinTech", "UK", "2024-01-20", "ads", "Pro", 2, False, True),
    ("A3", "Co3", "EdTech", "US", "2024-02-05", "event", "Enterprise", 1, False, False),
    ("A4", "Co4", "DevTools", "DE", "2024-02-15", "organic", "Basic", 1, False, True),
]
SUBS = [  # id, acct, start, end, plan, seats, mrr, trial
    ("S1", "A1", "2024-01-10", None, "Basic", 2, 38, False),
    ("S2", "A1", "2024-02-15", None, "Pro", 1, 49, False),
    ("S3", "A2", "2024-01-20", "2024-03-15", "Pro", 2, 98, False),
    ("S4", "A3", "2024-02-05", None, "Enterprise", 1, 199, False),
    ("S5", "A4", "2024-02-15", "2024-04-10", "Basic", 1, 19, False),
    ("S6", "A3", "2024-02-05", None, "Enterprise", 1, 0, True),
]
EVENTS = [  # id, acct, date, reason
    ("E1", "A2", "2024-03-15", "pricing"), ("E2", "A4", "2024-04-10", "features"),
    ("E3", "A3", "2024-03-01", "competitor"), ("E4", "A2", "2024-03-15", "pricing"),  # E4 duplicates E1
]
TICKETS = [  # id, acct, submitted, closed, res_hours, priority, first_resp_min, csat, esc
    ("T1", "A1", "2024-02-01 10:00:00", "2024-02-02 10:00:00", 24, "high", 10, 5, False),
    ("T2", "A1", "2024-02-10 10:00:00", "2024-02-10 14:00:00", 4, "low", 30, None, False),
    ("T3", "A2", "2024-02-11 09:00:00", "2024-02-12 09:00:00", 24, "urgent", 60, 4, True),
]
USAGE = [  # id, sub, date, feature, count, secs, errors, beta
    ("U1", "S1", "2024-02-01", "reports", 5, 100, 0, False), ("U2", "S1", "2024-02-02", "export", 2, 50, 1, False),
    ("U3", "S2", "2024-02-20", "reports", 3, 60, 0, False), ("U4", "S3", "2024-04-30", "reports", 7, 70, 0, False),
]


def write_tiny(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(ACCOUNTS, columns=["account_id", "account_name", "industry", "country", "signup_date", "referral_source", "plan_tier", "seats", "is_trial", "churn_flag"]).to_csv(d / "ravenstack_accounts.csv", index=False)
    pd.DataFrame([(s[0], s[1], s[2], s[3], s[4], s[5], s[6], s[6] * 12, s[7], False, False, s[3] is not None, "monthly", True) for s in SUBS],
                 columns=["subscription_id", "account_id", "start_date", "end_date", "plan_tier", "seats", "mrr_amount", "arr_amount", "is_trial", "upgrade_flag", "downgrade_flag", "churn_flag", "billing_frequency", "auto_renew_flag"]).to_csv(d / "ravenstack_subscriptions.csv", index=False)
    pd.DataFrame([(e[0], e[1], e[2], e[3], 0.0, False, False, False, None) for e in EVENTS],
                 columns=["churn_event_id", "account_id", "churn_date", "reason_code", "refund_amount_usd", "preceding_upgrade_flag", "preceding_downgrade_flag", "is_reactivation", "feedback_text"]).to_csv(d / "ravenstack_churn_events.csv", index=False)
    pd.DataFrame(TICKETS, columns=["ticket_id", "account_id", "submitted_at", "closed_at", "resolution_time_hours", "priority", "first_response_time_minutes", "satisfaction_score", "escalation_flag"]).to_csv(d / "ravenstack_support_tickets.csv", index=False)
    pd.DataFrame(USAGE, columns=["usage_id", "subscription_id", "usage_date", "feature_name", "usage_count", "usage_duration_secs", "error_count", "is_beta_feature"]).to_csv(d / "ravenstack_feature_usage.csv", index=False)
    return d


@pytest.fixture()
def tiny_raw(tmp_path):
    return write_tiny(tmp_path / "raw")


@pytest.fixture()
def tiny_con(tiny_raw, tmp_path):
    db = tmp_path / "tiny.duckdb"
    build_database(tiny_raw, db)
    con = open_readonly(db)
    yield con
    con.close()


ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def real_raw():
    p = ROOT / "data" / "raw"
    if not (p / "ravenstack_accounts.csv").exists():
        pytest.skip("full dataset not present in data/raw")
    return p


@pytest.fixture(scope="session")
def real_con(real_raw, tmp_path_factory):
    db = tmp_path_factory.mktemp("real") / "real.duckdb"
    build_database(real_raw, db)
    con = open_readonly(db)
    yield con
    con.close()
