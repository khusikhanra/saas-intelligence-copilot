# Decisions and assumptions

| # | Decision | Why |
|---|---|---|
| 1 | **MRR = sum of `mrr_amount` over paid subscription rows open on the date.** Alternatives (one row per account; excluding churn-flagged accounts) are computed and displayed. | The data holds ~10 rows/account and cannot tell us whether they are concurrent line items or successive periods. The choice moves MRR ~7x, so it is made visible rather than hidden. |
| 2 | A subscription ending on date D is **not** active on D. Trials (`is_trial`) are excluded from MRR and paying counts. | Consistent boundary rule; trial rows have MRR 0. Tested. |
| 3 | **Primary churn label = `accounts.churn_flag`**; events are used for timing, reasons and cohorts. Both are exposed. | The flag is the only account-level label for segment comparison; events conflict with it for 62% of accounts (DQ-01), so neither is treated as truth. |
| 4 | Snapshot date = latest date in any table. | Avoids using "today" on historical data. |
| 5 | Cleaning **adds flags, never deletes rows.** Duplicate churn events are flagged and excluded from event metrics only. | Preserves traceability; every exclusion is documented and testable. |
| 6 | Revenue-by-plan uses `subscriptions.plan_tier`; churn-by-plan uses `accounts.plan_tier` (labelled). | The two disagree for 66% of accounts (DQ-07). |
| 7 | Usage and support are used as **lifetime per-account aggregates** only. | Usage dates fall outside subscription windows (DQ-04) and tickets predate signup (DQ-08), so time-aligned analysis would be invalid. |
| 8 | Churn comparisons report counts, Wilson intervals and chi-square / Mann-Whitney p-values; p between 0.05 and 0.10 is "suggestive", never "significant". No multiple-comparison correction. | Prevents over-reading noise in small segments; the lack of correction is disclosed. |
| 9 | LLM may only choose a catalog analysis or write SQL; explanations are accepted only if every number traces to the result. | Removes the main hallucination path. |
| 10 | Offline keyword router when no key is set. | App stays useful and testable without credentials; the answers are real computations, not canned text. |
| 11 | No file-upload feature. | Not needed (fixed dataset); removes an attack surface. |
| 12 | `high_value_at_risk` is a transparent **rule** (top-quartile MRR + churn event in last 90 days), not a model. | No predictor of churn was found in the data, so a score would be unjustified. |
| 13 | Dependencies pinned to the tested versions. | Reproducibility. |

## Bugs found by verification (and fixed)

- Cohort headline crashed on short histories (found by the hand-built test dataset).
- Grounding check accepted invented percentages because a whole-number tolerance was applied to fractions.
- SQL comments survived regeneration (contradicting the intended guarantee); now stripped.
- Bad values were reported late and inconsistently (DuckDB parses lazily); now a clean `SchemaError` at load.
- Demo-sample generator dropped `churn_flag` under pandas 3 (only visible from a fresh clone).
- Default date filter silently excluded accounts (visible only in a screenshot: churn showed 21.9% instead of 22.0%).
