import time

import pytest

from src.analytics.db import QueryError, QueryTimeout, run_query
from src.security.input_validation import InputError, clean_question
from src.security.logging_utils import log_event, redact
from src.security.sql_guard import SQLValidationError, apply_row_limit, validate_sql

GOOD = [
    "select industry, count(*) from accounts group by 1 order by 2 desc",
    "with a as (select account_id, sum(mrr_amount) m from subscriptions where end_date is null group by 1) select * from a order by m desc limit 5",
    "select date_trunc('month', start_date) m, sum(mrr_amount) from subscriptions group by 1",
    "select a.industry, avg(f.total_usage_count) from account_features f join accounts a using(account_id) group by 1",
    "select quantile_cont(seats, 0.5) from accounts",
    "select industry from accounts union all select country from accounts",
]
BAD = [
    "drop table accounts", "delete from accounts", "update accounts set seats = 1", "insert into accounts select * from accounts",
    "create table x as select 1", "alter table accounts add column z int", "copy accounts to '/tmp/x.csv'", "pragma database_list",
    "attach 'x.db'", "install httpfs", "load httpfs", "set threads = 1", "select 1; drop table accounts", "select * from accounts; select * from subscriptions",
    "select * from read_csv('x.csv')", "select * from read_parquet('x.parquet')", "select * from '/etc/passwd'", "select * from 'x.csv'",
    "select * from main.accounts", "select * from information_schema.tables", "select * from duckdb_tables()", "select * from range(1000000000000)",
    "select unnest(generate_series(1, 10)) from accounts", "select getenv('HOME') from accounts", "select * from accounts a, subscriptions s",
    "select * from accounts cross join subscriptions", "select * from accounts a join subscriptions s on 1=1", "select * from nonexistent",
    "select * from _meta", "select * from _snapshot", "select 1", "", "   ", "select * from accounts into x", "with x as (select * from read_json('a')) select * from x",
    "select * from accounts where seats > (select count(*) from read_csv('a.csv'))", "select sleep_fn(5) from accounts", "select * from accounts --; drop table accounts;\n; drop table x",
    "SeLeCt * FrOm AcCoUnTs; DrOp TaBlE accounts", "select * from accounts /* x */; truncate table accounts",
]


@pytest.mark.parametrize("q", GOOD)
def test_legitimate_queries_accepted(q):
    v = validate_sql(q)
    assert v.tables and v.sql


@pytest.mark.parametrize("q", BAD)
def test_unsafe_or_invalid_queries_rejected(q):
    with pytest.raises(SQLValidationError):
        validate_sql(q)


def test_non_string_and_oversized_rejected():
    for bad in (None, 123, ["select 1"], "select " + ",".join(["industry"] * 3000) + " from accounts"):
        with pytest.raises(SQLValidationError):
            validate_sql(bad)  # type: ignore[arg-type]


def test_fanout_warning():
    w = validate_sql("select account_id, sum(mrr_amount) from subscriptions s join feature_usage u using(subscription_id) group by 1").warnings
    assert w and "granularity" in w[0]
    assert not validate_sql("select account_id, sum(mrr_amount) from subscriptions group by 1").warnings
    assert not validate_sql("select count(distinct s.account_id) from subscriptions s join feature_usage u using(subscription_id)").warnings


def test_executed_sql_is_regenerated_from_validated_ast():
    v = validate_sql("select industry /* hidden */ from accounts -- trailing")
    assert "hidden" not in v.sql and "trailing" not in v.sql


def test_row_limit_applied_and_truncation_flagged(tiny_con):
    assert "LIMIT 3" in apply_row_limit(validate_sql("select * from feature_usage"), 2).upper().replace("  ", " ")
    r = run_query(tiny_con, "select * from feature_usage", max_rows=2)
    assert len(r.df) == 2 and r.truncated
    r = run_query(tiny_con, "select * from feature_usage", max_rows=100)
    assert len(r.df) == 4 and not r.truncated
    r = run_query(tiny_con, "select * from accounts union all select * from accounts", max_rows=5)
    assert len(r.df) == 5 and r.truncated


def test_execution_errors_are_friendly(tiny_con):
    with pytest.raises(QueryError, match="query failed"):
        run_query(tiny_con, "select no_such_column from accounts")
    with pytest.raises(SQLValidationError):
        run_query(tiny_con, "drop table accounts")
    assert tiny_con.execute("select count(*) from accounts").fetchone()[0] == 4


def test_timeout_cancels_expensive_query(real_con):
    t0 = time.time()
    with pytest.raises(QueryTimeout):
        run_query(real_con, "select count(*) from feature_usage a join feature_usage b on a.usage_count >= b.usage_count "
                            "join feature_usage c on a.usage_count >= c.usage_count", timeout=1)
    assert time.time() - t0 < 6


def test_database_denies_file_access_even_if_validator_were_bypassed(tiny_con):
    import duckdb
    for stmt in ("select * from read_csv('/etc/passwd')", "copy (select 1) to '/tmp/leak.csv'", "set enable_external_access = true"):
        with pytest.raises(duckdb.Error):
            tiny_con.cursor().execute(stmt)


def test_input_validation():
    assert clean_question("  What   is\tMRR?\x00 ") == "What is MRR ?".replace(" ?", "?")
    for bad in ("", "   ", None, 42, "x" * 501):
        with pytest.raises(InputError):
            clean_question(bad)


def test_logging_never_records_question_sql_or_keys(caplog):
    import logging
    with caplog.at_level(logging.INFO, logger="saas_copilot"):
        log_event("t", question="secret question", sql="select secret", api_key="sk-abcdefghijklmnop", note="key sk-abcdefghijklmnopqrst here", n=3)
    text = caplog.text
    assert "secret question" not in text and "select secret" not in text and "sk-abcdefghijklmnop" not in text and "n=3" in text
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in redact("x sk-abcdefghijklmnopqrstuvwxyz y")
