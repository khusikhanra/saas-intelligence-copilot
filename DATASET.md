# Dataset notes

- **Name:** RavenStack SaaS Subscription & Churn Analytics (River @ Rivalytics)
- **URL:** https://www.kaggle.com/datasets/rivalytics/saas-subscription-and-churn-analytics-dataset
- **Files (as supplied):** ravenstack_accounts.csv (500 x 10), ravenstack_subscriptions.csv (5,000 x 14), ravenstack_churn_events.csv (600 x 9), ravenstack_support_tickets.csv (2,000 x 9), ravenstack_feature_usage.csv (25,000 x 8)
- **Date range:** signups/subscriptions from 2023-01 through 2024-12-31 (snapshot date is derived from the data, not today's date).
- **Synthetic?** Described as synthetic by its public listing; company names are placeholders (e.g. "Company_0"). The supplied ZIP had no README, so this is *not confirmed by the package*.
- **Licence:** not included in the ZIP. Confirm on the Kaggle page; credit the author.
- **Integrity:** raw files are never modified; SHA-256 checksums are written to `data/processed/raw_manifest.json` at build time.
