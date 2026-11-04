"""Create a small, self-consistent demo sample (default 60 accounts) from the full raw CSVs.
Keeps all child rows of the sampled accounts so every foreign key stays valid. Usage: python scripts/make_sample.py"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW, OUT = ROOT / "data" / "raw", ROOT / "data" / "sample"


def main(n_accounts: int = 60, seed: int = 7) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    acc = pd.read_csv(RAW / "ravenstack_accounts.csv")
    # stratify on the churn flag so both outcomes are represented
    # iterate groups explicitly: groupby().apply() drops the grouping column in pandas >= 3
    parts = [g.sample(max(1, round(n_accounts * len(g) / len(acc))), random_state=seed) for _, g in acc.groupby("churn_flag")]
    sample = pd.concat(parts).sort_values("account_id")
    ids = set(sample["account_id"])
    subs = pd.read_csv(RAW / "ravenstack_subscriptions.csv")
    subs = subs[subs["account_id"].isin(ids)]
    out = {"ravenstack_accounts.csv": sample, "ravenstack_subscriptions.csv": subs,
           "ravenstack_churn_events.csv": pd.read_csv(RAW / "ravenstack_churn_events.csv").query("account_id in @ids"),
           "ravenstack_support_tickets.csv": pd.read_csv(RAW / "ravenstack_support_tickets.csv").query("account_id in @ids"),
           "ravenstack_feature_usage.csv": pd.read_csv(RAW / "ravenstack_feature_usage.csv").query("subscription_id in @sids", local_dict={"sids": set(subs["subscription_id"])})}
    for name, df in out.items():
        df.to_csv(OUT / name, index=False)
        print(f"{name}: {len(df):,} rows")


if __name__ == "__main__":
    main(*(int(a) for a in sys.argv[1:2]))
