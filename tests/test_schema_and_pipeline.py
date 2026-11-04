import hashlib
import shutil

import pandas as pd
import pytest

from src.data.audit import run_audit
from src.data.build_db import build_database
from src.data.schema import SchemaError, validate_files
from src.analytics.db import open_readonly


def test_valid_files_pass(tiny_raw):
    assert set(validate_files(tiny_raw)) == {"accounts", "subscriptions", "churn_events", "support_tickets", "feature_usage"}


def test_missing_file_rejected(tiny_raw):
    (tiny_raw / "ravenstack_churn_events.csv").unlink()
    with pytest.raises(SchemaError, match="missing file"):
        validate_files(tiny_raw)


def test_missing_and_extra_columns_rejected(tiny_raw):
    p = tiny_raw / "ravenstack_accounts.csv"
    df = pd.read_csv(p).drop(columns=["industry"])
    df["surprise"] = 1
    df.to_csv(p, index=False)
    with pytest.raises(SchemaError) as e:
        validate_files(tiny_raw)
    assert "missing columns ['industry']" in str(e.value) and "unexpected columns ['surprise']" in str(e.value)


def test_bad_values_rejected_not_silently_coerced(tiny_raw, tmp_path):
    p = tiny_raw / "ravenstack_subscriptions.csv"
    df = pd.read_csv(p)
    df["start_date"] = df["start_date"].astype(object)
    df.loc[0, "start_date"] = "not-a-date"
    df.to_csv(p, index=False)
    with pytest.raises(SchemaError, match="do not match expected types"):
        build_database(tiny_raw, tmp_path / "x.duckdb")
    assert not (tmp_path / "x.duckdb").exists()  # failed build leaves no half-built database


def test_row_counts_and_no_fanout(tiny_con):
    n = lambda t: tiny_con.execute(f"select count(*) from {t}").fetchone()[0]
    assert (n("accounts"), n("subscriptions"), n("churn_events"), n("support_tickets"), n("feature_usage")) == (4, 6, 4, 3, 4)
    assert n("account_features") == n("accounts") == 4   # marts must be one row per account
    assert n("sub_enriched") == n("subscriptions")


def test_cleaning_flags_but_never_deletes(tiny_con):
    assert tiny_con.execute("select count(*) from churn_events where dq_duplicate_account_date").fetchone()[0] == 1
    assert tiny_con.execute("select count(*) from churn_events").fetchone()[0] == 4
    assert tiny_con.execute("select count(*) from accounts where dq_churn_flag_conflict").fetchone()[0] == 1  # A3: event but not flagged
    assert tiny_con.execute("select dq_after_subscription_end from feature_usage where usage_id='U4'").fetchone()[0] is True


def test_raw_files_unchanged_by_pipeline(tiny_raw, tmp_path):
    h = lambda: {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tiny_raw.glob("*.csv")}
    before = h()
    build_database(tiny_raw, tmp_path / "a.duckdb", tmp_path / "manifest.json")
    assert h() == before
    assert (tmp_path / "manifest.json").exists()


def test_audit_detects_known_issues(tiny_raw):
    a = run_audit(tiny_raw)
    ids = {i["id"]: i for i in a["issues"]}
    assert ids["DQ-01"]["affected"] == 1 and ids["DQ-01"]["total"] == 4
    assert ids["DQ-12"]["affected"] == 1                      # duplicate churn event
    assert ids["DQ-04"]["affected"] >= 1                      # usage after sub end
    assert any("unique" in c["check"] for c in a["passed_checks"])
    assert {p["table"] for p in a["profile"]} >= {"accounts", "subscriptions"}


def test_audit_detects_duplicates_and_orphans(tiny_raw):
    p = tiny_raw / "ravenstack_subscriptions.csv"
    df = pd.read_csv(p)
    df = pd.concat([df, df.iloc[[0]]], ignore_index=True)           # duplicate subscription_id
    df.loc[len(df) - 2, "account_id"] = "GHOST"                       # orphan foreign key
    df.to_csv(p, index=False)
    ids = {i["id"] for i in run_audit(tiny_raw)["issues"]}
    assert "DQ-PK-subscriptions" in ids and "DQ-FK-subscriptions" in ids


def test_database_connection_is_read_only(tiny_con):
    import duckdb
    for stmt in ("create table x as select 1", "delete from accounts", "drop table accounts", "update accounts set seats = 99"):
        with pytest.raises(duckdb.Error):
            tiny_con.cursor().execute(stmt)


def test_real_data_reconciles(real_raw, real_con):
    n = lambda t: real_con.execute(f"select count(*) from {t}").fetchone()[0]
    assert (n("accounts"), n("subscriptions"), n("churn_events"), n("support_tickets"), n("feature_usage")) == (500, 5000, 600, 2000, 25000)
    assert n("account_features") == 500


def test_bundled_demo_sample_is_valid_and_builds(tmp_path):
    """Regression: the committed sample must pass strict validation and build (fresh clones rely on it)."""
    from tests.conftest import ROOT
    stats = build_database(ROOT / "data" / "sample", tmp_path / "sample.duckdb")
    assert stats["accounts"] >= 30 and stats["account_features"] == stats["accounts"]
    a = run_audit(ROOT / "data" / "sample")
    assert not [i for i in a["issues"] if i["id"].startswith(("DQ-PK", "DQ-FK"))]   # sample keeps referential integrity
