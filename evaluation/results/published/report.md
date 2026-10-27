# Evaluation report — 20261027T193420Z

- Model: `gemini-3.5-flash-lite` · prompt `receipt-extract-v1` · passes 1 · mode **replay**
- Dataset: 56 documents (52 labelled receipts, 1 non-receipt, 3 exact duplicates) · manifest sha256 `0c90b8f151bf`
- Git commit: `c484d33` · started 2026-10-27T19:34:20+00:00
- Model outputs were recorded live in run `20261027T192259Z` and re-scored here without new calls.

Synthetic, rendered receipts (see `evaluation/dataset/README.md`). Treat these numbers as performance on clean printed receipts with planted difficulties, not as real-world photo accuracy.

## Schema validity vs. correctness

| Metric | Result |
|---|---|
| Model calls attempted | 53 |
| Schema-valid responses | 100.0% (53/53) |
| Extraction failures | 0.0% (0/53)  |
| Key-field accuracy (micro, 6 fields) | 99.7% (311/312) |
| Exact-match receipts (all key fields + all line items) | 98.1% (51/52) |

## Field accuracy

| Field | Accuracy |
|---|---|
| merchant | 100.0% (52/52) |
| date | 98.1% (51/52) |
| currency | 100.0% (52/52) |
| subtotal | 100.0% (52/52) |
| tax | 100.0% (52/52) |
| total | 100.0% (52/52) |
| tip | 100.0% (52/52) |
| discount | 100.0% (52/52) |
| service_charge | 100.0% (52/52) |
| transaction_reference | 100.0% (52/52) |

## Line items

Precision 100.0% · recall 100.0% · F1 100.0% · matched lines with correct qty and unit price 96.6% (142/147)

## Validation and review

| Metric | Result |
|---|---|
| Reconciliation pass rate (checkable predictions) | 94.0% (47/50) |
| Planted arithmetic errors caught | 100.0% (3/3) |
| False reconciliation alarms on consistent receipts | 0.0% (0/47) |
| Flag recall: wrong key-field values flagged | 100.0% (1/1) |
| Review load: correct values flagged anyway | 8.7% (27/311) |
| Designed hard fields flagged | 88.0% (22/25) |
| Receipts with a wrong key field sent to review | 100.0% (1/1) |
| Receipt-level review detection P / R / F1 | 96.0% / 100.0% / 98.0% |


> **Small sample:** Flag recall is computed from only 1 case(s); it is not a reliable estimate.
> **Small sample:** Planted-error catch rate is computed from only 3 case(s); it is not a reliable estimate.

## Duplicate detection

| Kind | Precision | Recall |
|---|---|---|
| exact | 100.0% | 100.0% |
| probable | 100.0% | 100.0% |
| any | 100.0% | 100.0% |

## Latency and tokens (first pass, successful calls)

p50 3650 ms · p95 4990 ms · n=53 · mean input tokens 1571 · mean output tokens 773. No cost is estimated.

## Errors on key fields

| Case | Field | Truth | Predicted | Flagged |
|---|---|---|---|---|
| r036 | date | `2026-03-06` | `2026-03-03` | yes |
