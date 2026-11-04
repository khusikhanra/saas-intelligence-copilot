"""Validation of AI-generated SQL before it is allowed anywhere near the database.

Defence in depth (any one layer failing should not be enough to cause harm):
  1. This validator: single read-only SELECT, allowlisted tables/functions, no unconstrained joins, size caps.
  2. We execute the SQL regenerated from the validated AST (not the user's raw text) -> no parser differential.
  3. The DuckDB connection is opened READ_ONLY with external file access disabled and a memory limit.
  4. Row limit and wall-clock timeout are enforced at execution time (src/analytics/db.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

from src import config
from src.data.build_db import ALLOWED_OBJECTS

ALLOWED_TABLES = frozenset(ALLOWED_OBJECTS)

_FORBIDDEN_NODE_NAMES = ["Insert", "Update", "Delete", "Create", "Drop", "Alter", "AlterTable", "Command", "Pragma", "Set", "Copy",
                         "Attach", "Detach", "Merge", "Use", "Install", "Commit", "Rollback", "Transaction", "Into", "Lock",
                         "LoadData", "Export", "TruncateTable", "ReadCSV", "GenerateSeries", "Show", "Describe", "Analyze", "Kill"]
FORBIDDEN_NODES = tuple(getattr(exp, n) for n in _FORBIDDEN_NODE_NAMES if hasattr(exp, n))

# Functions that sqlglot does not model (parsed as Anonymous) but are safe and useful for analytics.
SAFE_ANONYMOUS = frozenset({
    "quantile_cont", "quantile_disc", "quantile", "mode", "corr", "covar_pop", "covar_samp", "regr_slope", "regr_intercept", "regr_r2",
    "date_part", "make_date", "last_day", "monthname", "dayname", "epoch", "isfinite", "isnan", "bool_and", "bool_or", "histogram",
    "ifnull", "nullif", "greatest", "least", "arg_min", "arg_max", "min_by", "max_by", "list_aggr", "date_diff", "date_sub", "date_add",
    "year", "month", "quarter", "day", "week", "dayofweek", "strftime", "strptime", "printf", "format", "round", "floor", "ceil",
    "sign", "abs", "sqrt", "ln", "log", "log10", "pow", "power", "exp", "mean", "median", "stddev", "stddev_pop", "stddev_samp",
    "variance", "var_pop", "var_samp", "count_if", "approx_count_distinct", "list", "array_agg", "string_agg", "len", "length",
    "lower", "upper", "trim", "ltrim", "rtrim", "replace", "concat", "concat_ws", "substring", "substr", "left", "right", "contains",
    "starts_with", "ends_with", "coalesce", "if", "typeof", "try_cast", "to_months", "to_days", "to_years", "age", "date_trunc",
})

MAX_JOINS = 6
MAX_NODES = 2500
_FACT_TABLES_FINER_THAN_SUBS = {"feature_usage", "support_tickets", "churn_events"}


class SQLValidationError(ValueError):
    """The SQL was rejected; the message is safe to show to the user."""


@dataclass
class ValidatedQuery:
    sql: str                      # regenerated from the validated AST, without LIMIT applied
    tables: set = field(default_factory=set)
    warnings: list = field(default_factory=list)
    root: exp.Expression | None = None


def _cte_names(root: exp.Expression) -> set:
    return {c.alias_or_name.lower() for c in root.find_all(exp.CTE)}


def validate_sql(sql: str, max_chars: int | None = None) -> ValidatedQuery:
    max_chars = max_chars or config.MAX_SQL_CHARS
    if not isinstance(sql, str) or not sql.strip():
        raise SQLValidationError("Empty query.")
    if len(sql) > max_chars:
        raise SQLValidationError(f"Query is too long (limit {max_chars} characters).")
    if "\x00" in sql:
        raise SQLValidationError("Query contains invalid characters.")
    try:
        statements = [s for s in sqlglot.parse(sql, dialect="duckdb") if s is not None]
    except (ParseError, TokenError) as exc:
        raise SQLValidationError(f"Could not parse the SQL: {str(exc).splitlines()[0][:160]}") from exc
    if len(statements) != 1:
        raise SQLValidationError("Exactly one SQL statement is allowed.")
    root = statements[0]
    if not isinstance(root, (exp.Select, exp.SetOperation)):
        raise SQLValidationError("Only read-only SELECT queries are allowed.")

    nodes = list(root.walk())
    if len(nodes) > MAX_NODES:
        raise SQLValidationError("Query is too complex.")
    for node in nodes:
        n = node[0] if isinstance(node, tuple) else node
        if isinstance(n, FORBIDDEN_NODES):
            raise SQLValidationError(f"Statement type or function not permitted: {type(n).__name__}.")

    ctes = _cte_names(root)
    tables: set = set()
    for t in root.find_all(exp.Table):
        if not isinstance(t.this, exp.Identifier):
            raise SQLValidationError("Table functions and file paths are not permitted in FROM.")
        if t.args.get("db") or t.args.get("catalog"):
            raise SQLValidationError("Schema-qualified tables are not permitted.")
        name = t.name.lower()
        if name in ctes:
            continue
        if name not in ALLOWED_TABLES:
            raise SQLValidationError(f"Table '{t.name}' is not available. Allowed: {', '.join(sorted(ALLOWED_TABLES))}.")
        tables.add(name)
    if not tables:
        raise SQLValidationError("The query must read from at least one allowed table.")

    for fn in root.find_all(exp.Anonymous):
        if str(fn.this).lower() not in SAFE_ANONYMOUS:
            raise SQLValidationError(f"Function '{fn.this}' is not permitted.")

    joins = list(root.find_all(exp.Join))
    if len(joins) > MAX_JOINS:
        raise SQLValidationError(f"Too many joins (limit {MAX_JOINS}).")
    for j in joins:
        on = j.args.get("on")
        if not (on or j.args.get("using")):
            raise SQLValidationError("Joins must include an ON or USING condition (unconstrained joins create huge cross products).")
        if on is not None and not list(on.find_all(exp.Column)):
            raise SQLValidationError("Join conditions must compare columns (constant conditions such as ON 1=1 are cartesian products).")

    warnings = _fanout_warnings(root, ctes)
    return ValidatedQuery(sql=root.sql(dialect="duckdb", comments=False), tables=tables, warnings=warnings, root=root)


def _select_tables(sel: exp.Select, ctes: set) -> set:
    """Tables referenced directly in this SELECT's own FROM/JOIN (not nested subqueries)."""
    out = set()
    for source in [sel.args.get("from_")] + [j.this for j in sel.args.get("joins", [])]:
        if source is None:
            continue
        node = source.this if isinstance(source, exp.From) else source
        if isinstance(node, exp.Table) and node.name.lower() not in ctes:
            out.add(node.name.lower())
    return out


def _fanout_warnings(root: exp.Expression, ctes: set) -> list:
    """Heuristic: aggregating subscription-level amounts or accounts after joining a finer-grained table duplicates rows."""
    warns = []
    for sel in root.find_all(exp.Select):
        tbls = _select_tables(sel, ctes)
        fine = tbls & _FACT_TABLES_FINER_THAN_SUBS
        coarse = tbls & {"subscriptions", "accounts", "account_features", "sub_enriched"}
        if not (fine and coarse):
            continue
        aggs = [a for a in sel.find_all(exp.AggFunc) if not (isinstance(a, exp.Count) and isinstance(a.this, exp.Distinct))]
        has_sum_like = any(isinstance(a, (exp.Sum, exp.Avg, exp.Count)) for a in aggs)
        if has_sum_like:
            warns.append("This query joins tables of different granularity (" + ", ".join(sorted(tbls)) +
                         ") and aggregates the result; sums, averages and counts may be duplicated. "
                         "Pre-aggregate each table to one row per key (see account_features) or use COUNT(DISTINCT ...).")
            break
    return warns


def apply_row_limit(vq: ValidatedQuery, max_rows: int) -> str:
    """Return SQL with a LIMIT of max_rows + 1 (one extra row lets the caller detect truncation)."""
    root = vq.root.copy()
    if isinstance(root, exp.Select):
        lim = root.args.get("limit")
        existing = None
        if lim is not None and isinstance(lim.expression, exp.Literal) and lim.expression.is_int:
            existing = int(lim.expression.name)
        if existing is None or existing > max_rows:
            root = root.limit(max_rows + 1)
        return root.sql(dialect="duckdb", comments=False)
    return f"SELECT * FROM ({root.sql(dialect='duckdb', comments=False)}) AS _q LIMIT {max_rows + 1}"
