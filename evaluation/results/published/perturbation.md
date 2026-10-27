# Validator perturbation test

Source outputs: `evaluation/results/published/raw_outputs.jsonl` (recorded Gemini replies; no new calls).
Each row injects one realistic misread into an otherwise correct, reconciling receipt and checks whether the
deterministic rules flag the corrupted field. This measures the validators, not the model.

| Injected error | Caught | Rate |
|---|---|---|
| currency: wrong valid code | 0/43 | 0% |
| date: day off by one | 0/33 | 0% |
| date: year +1 (future) | 33/33 | 100% |
| line total: one wrong digit | 43/44 | 98% |
| subtotal: one wrong digit | 43/43 | 100% |
| tax: one wrong digit | 21/31 | 68% |
| total: one wrong digit | 44/44 | 100% |
| total: two digits swapped | 44/44 | 100% |

Overall: 228/315 injected errors flagged.

Arithmetic errors are caught because they break a sum; a wrong value that keeps the arithmetic consistent
(a plausible date one day off, a different but valid currency) cannot be caught by rules alone. Those need a
second opinion (two-pass disagreement, OCR) or the reviewer. Tax misreads on tax-inclusive receipts are
also missed: the VAT line is informational there, so a wrong figure does not change any sum.
