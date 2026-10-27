# Interview preparation: AI Expense Scanner

Questions an interviewer for an AI Engineer / Applied AI / AI full-stack role is likely to ask about this
project, with answers grounded in the code. File paths point to where each answer lives. Where something is
*not* built, the answer says so. Measured numbers are in `evaluation/results/published/report.md`; section K
summarises them.

---

## A. Problem and architecture

**A1. What problem does this project solve?**
Turning receipt photos and PDFs into expense records you can trust. The hard part is not calling a vision model
(that is one function) but knowing *when the model is wrong* before the number reaches your books. The system
extracts with Gemini, checks every value with deterministic rules, flags what it doubts with a stated reason,
and lets a person fix it in one action. Every fix is logged, so it doubles as labelled data.
*Follow-up:* "Why not just trust a good model?" → Because a schema guarantees shape, not truth. A model that misreads
`18.23` as `13.23` still returns perfectly valid JSON.

**A2. Walk me through the architecture.**
React SPA → FastAPI → PostgreSQL. Upload stores the file and a `queued` row and returns `202`. A worker
(a thread inside the API by default, `backend/app/worker.py`) claims rows with `SELECT … FOR UPDATE SKIP LOCKED`,
runs the pipeline (`services/pipeline.py`): preprocess → **model gateway** (`llm/gateway.py`, the only code
that talks to Gemini) → Pydantic → normalisation → validators → uncertainty rules → duplicate check → persist.
The reviewer works against `/api/receipts/{id}` endpoints that re-run the same analysis after every edit.
Diagram: `WORKFLOW.md` §2.

**A3. Why separate frontend and backend?**
The Gemini key must never reach the browser, so model calls have to happen on a server. Separating also lets the
static frontend be served from a CDN (Vercel) while the API scales and deploys on its own (Render). The only
contract between them is the REST API and its error envelope.

**A4. Why is extraction asynchronous?**
A Gemini call takes seconds, sometimes tens of seconds with retries (the evaluation saw 5–35 s per document under
rate limiting). Holding an HTTP request open that long is fragile, and retries would multiply it. So the API
returns `202` immediately and the UI polls only while the receipt is `queued`/`processing`.
*Trade-off:* the worker runs in the web process to fit one free service; under load it should be a separate
process (`python -m app.worker`, same code).

**A5. How does a receipt move through the system?** `queued → processing → needs_review → approved/rejected`,
with `failed` for processing errors. Transitions are guarded in `services/review.py::_require`, and the state
machine is drawn in `WORKFLOW.md` §4.

## B. Multimodal AI and Gemini

**B1. How does Gemini read a receipt?**
It is a multimodal model: image pixels are encoded into the same token space as text, so it reads layout and
characters together. We send each page as an image `Part` plus a system instruction and a JSON schema
(`llm/providers.py::GeminiProvider.generate`). There is no separate OCR step.

**B2. How do you send images and PDFs?**
Images are EXIF-rotated, capped at 2048 px on the long side, and re-encoded to JPEG (`services/files.py::prepare_pages`).
That bounds tokens and strips metadata such as GPS. PDFs are rendered page-by-page to images with pypdfium2 and sent in
order, labelled "Page N". Gemini can take PDFs directly; I rasterise so images and PDFs go through one path,
preprocessing is identical, and the page count is controlled (`MAX_PDF_PAGES`).

**B3. Why structured outputs?**
They make the reply parseable: `response_mime_type="application/json"` plus `response_json_schema` generated from the
Pydantic model `llm/schema.py::ReceiptExtraction`. Without them you parse prose or hope the model follows a format.
I still validate the reply with Pydantic (`gateway.parse_reply`) because the application must not depend on the
provider's enforcement.

**B4. Why can valid JSON still be wrong?**
The schema says "total is a string or null". It cannot say "total is the number printed at the bottom". The model
can transpose digits, read a subtotal as the total, pick the wrong date order, or fill a field from context.
That is why `validation.py` re-checks the arithmetic, and why the evaluation reports schema validity and
accuracy as separate numbers.

**B5. How did you stop the model from inventing values?**
Three things. Every field is nullable, so "unreadable" is representable and the model is not forced to fill a slot.
The prompt (`llm/prompts.py`) says never compute or infer a missing value. And `value` is separate from
`source_text`, so the reviewer sees exactly what was printed. The evaluation counts an invented value against a
null ground truth as wrong (`metrics.field_correct`).

**B6. What happens with an unreadable receipt?**
The model reports `readability: illegible` or `partially_legible` and returns nulls. Missing required fields become
`missing` and block approval; the readability observation becomes a review reason. If the document is not a
receipt (`is_receipt: false`), that is flagged too. A document that cannot be decoded at all is rejected at upload
(`corrupt_image`, `corrupt_pdf`).

**B7. How would you change models or providers?**
Model: set `GEMINI_MODEL`. Every log row and extraction stores `model` and `prompt_version`, so runs stay comparable.
Provider: write a class with `generate(ProviderRequest) -> ProviderResponse` and the same error categories
(`llm/providers.py`); nothing else changes, because routes and services never import a vendor SDK. I actually changed
models during development: the evaluation moved from `gemini-3.5-flash` to `gemini-3.5-flash-lite` when the free-tier
daily quota ran out (see K).

**B8. Why rasterise instead of using a separate OCR engine?**
Simplicity and one fewer dependency. The roadmap lists OCR (Tesseract/PaddleOCR) as an optional second opinion; it
is not implemented. It would be a good independent signal for doubt: disagreement between OCR text and the model's
`source_text` would flag a field.

## C. Data validation

**C1. What is Pydantic validation here?**
`ReceiptExtraction.model_validate(raw)` checks types, required keys, and enum values (`readability`,
`category_suggestion`). Failure is `schema_mismatch`, retried once, then the receipt is `failed` and the raw text is
kept in an `extractions` row with `schema_valid=false`. Nothing from it becomes data.

**C2. Schema validation vs business validation?**
Schema validation answers "is this the right shape?". Business validation answers "can these values be true
together?": does qty × price equal the line total, do the lines add up to the subtotal, does subtotal + tax − discount
equal the total, is the date in the past, is `XYZ` a currency. The second kind lives in `services/validation.py`, has
no model dependency, and has its own tests (`tests/test_validation.py`).

**C3. Why Decimal instead of float?**
Binary floats cannot represent 0.1 exactly: `0.1 + 0.2 != 0.3`. Summing many receipt lines drifts, and equality checks
fail spuriously. Every amount is parsed with `Decimal` (`services/money.py`) and stored as integer minor units (`BIGINT`)
plus an ISO 4217 code. The frontend formats minor units with string arithmetic, not floats (`lib/format.ts::formatMinor`).
*Follow-up:* "Why are line items `NUMERIC(18,4)` and not minor units?" → Fuel at 3.299/L has more precision than cents;
forcing it into minor units would either round (lying) or fail.

**C4. How do you reconcile subtotal, tax, discount and total?**
`check_total` tries four conventions: tax on top, tax included in prices, discount already inside the subtotal, or both.
It passes if any one matches within tolerance and reports *which* one matched (shown to the user). If there is no
subtotal, it uses the sum of line totals; if neither exists, it says the total could not be checked rather than passing.

**C5. How do you handle rounding?**
Tolerance is in the currency's minor unit: 1 unit, or 2 when a separately rounded tax line is involved. That is 0.01 for
USD, 1 for JPY, 0.001 for KWD (`money.currency_exponent`). Computed values (qty × price) round half-up; extracted values
are never rounded.

**C6. What if extracted fields contradict each other?**
The failing check implicates the fields involved: for a total mismatch, the total and every component present. They
become `uncertain` with the check's message as the reason. Nothing is changed automatically, because the system cannot
know which number is wrong. The reviewer corrects one, the checks re-run, and if they still fail, approval needs a written
note. That note is stored with the list of overridden checks.

**C7. How are amounts parsed from text?**
`money.parse_decimal` accepts symbols or codes only at the edges (`Rs. 1,250`, `12.50 EUR`), both separator conventions
(`1,234.56` / `1.234,56`), and accounting negatives `(4.00)`. A letter inside the number is an error: early on the parser
turned `1O.5O` (letter O) into `1.5`, a silently invented number, and a test caught it (`tests/test_money.py`).

## D. Human in the loop and uncertainty

**D1. When does a receipt need review?**
Every receipt lands in `needs_review` (auto-approval is opt-in via `AUTO_APPROVE_CLEAN_RECEIPTS`). Review *reasons*,
which drive the highlighting, come from `services/uncertainty.py::assess`: missing required field, unparseable value,
a failed check, unreadable or partially readable document, not a receipt, two passes disagreeing (optional), or a
probable duplicate.

**D2. How are uncertain fields represented?**
Each field row (`extraction_fields`) has `status` ∈ {extracted, uncertain, invalid, missing, verified} and a `reason`.
`extracted` means "read and passed every rule" but is still labelled **Unreviewed** in the UI. Only a human action sets
`verified`.

**D3. Why not ask the model for a confidence score?**
Gemini does not expose a calibrated per-field confidence here. Asking the model to rate itself produces a number that is
not calibrated. You cannot set a threshold on it and know what it means. So doubt is *measured*: failed rules, nulls,
parse errors, and optionally disagreement between two independent passes (`EXTRACTION_PASSES=2`). Each comes with a
reason the reviewer can check. The evaluation then measures how good these signals are (flag recall vs review load).

**D4. How are corrections stored?**
The `corrections` table records receipt, extraction, field, `old_value`, `new_value`, `action` (`corrected` or `confirmed`),
an optional note, and a timestamp. The model's original value stays in `extraction_fields.extracted_value` (the UI shows it
struck through). Decisions (approve, reject, duplicate resolution, reprocess) go to `review_decisions`, including any
checks that were overridden.
*Follow-up:* "How would you use the corrections?" → Export them as new evaluation cases and as few-shot examples. That
export is not built yet.

**D5. Can a reviewer approve something that fails a check?**
Yes, with a written reason, because receipts can be genuinely odd (a handwritten tip, a voucher). The override is recorded.
Approval is *blocked* only for things that make the expense row meaningless: a missing or invalid required field, any invalid
value, or an unresolved duplicate.

## E. Database and backend

**E1. Explain the schema.**
`receipts` (file metadata, SHA-256, status, denormalised merchant/date/currency/total for search and duplicate lookup) →
`extractions` (versioned; model, prompt version, raw output, schema validity) → `extraction_fields`, `line_items`,
`validation_checks`. Alongside: `corrections`, `review_decisions`, `expenses` (one per approved receipt), `model_calls`,
and `stored_files`. ER diagram: `WORKFLOW.md` §12.

**E2. Why version extractions?**
Re-running with a new model or prompt must not overwrite history. You need old outputs to compare versions and to debug.
`UNIQUE(receipt_id, version)` plus `current_extraction_id`.

**E3. What does the database enforce?**
Unique SHA-256, unique idempotency key, one expense per receipt, CHECK constraints on every status, currency shape, and
`total_minor >= 0` (refunds are `kind='refund'`). `tests/test_postgres.py` proves the constraints reject bad rows on real
Postgres.

**E4. How do transactions work?**
`db.session_scope()` commits on success and rolls back on any exception. Each pipeline run is one transaction, so either the
whole extraction (fields, lines, checks, status) lands, or none of it does. Model-call logs use a *separate* transaction,
so the history of a failed run survives the rollback.
*Trade-off:* the pipeline transaction stays open during the model call, which holds a connection for seconds. At higher
volume I would split it into claim → call outside a transaction → short write transaction.

**E5. How are duplicate submissions handled?**
Same bytes: the `UNIQUE(sha256)` constraint returns the existing receipt with no second model call. Same request
(double-click, network retry): the `Idempotency-Key` header, one per selected file in the browser. Races: both are unique
constraints, so the loser's `IntegrityError` is caught and the winner is returned.

**E6. How do two workers avoid processing the same receipt?**
`FOR UPDATE SKIP LOCKED` in `pipeline.claim_next`. A test starts six threads against Postgres and asserts six distinct
claims. A crashed worker's job is reclaimed after `JOB_LEASE_SECONDS`, and after `MAX_JOB_ATTEMPTS` it fails as
`abandoned` instead of looping forever.

**E7. How are files stored?**
Behind a `Storage` protocol (`services/storage.py`). The default is `database`: bytes go in `stored_files`, which survives
redeploys on hosts whose disk is ephemeral. `local` writes to disk. Object storage (S3/R2) is the right choice at scale
and is not implemented.

**E8. How does the API report failures?**
One envelope for everything: `{"error": {"code", "message", "details"}}` (`api/errors.py`). Codes are stable
(`unsupported_type`, `override_note_required`, `invalid_state`…) so the UI can react. Unhandled exceptions return a
generic 500 and are logged server-side. Processing failures are stored on the receipt as `error_code` plus a plain-language
`error_message`.

## F. Observability and reliability

**F1. What is a trace ID here?**
A UUID generated per extraction and shared by all its retry attempts. It is stored on each `model_calls` row and on the
`extractions` row, so you can go from a receipt to every provider attempt it caused.

**F2. Why log every attempt and not just the result?**
Retries hide problems. A receipt that "worked" after three 429s tells you about quota pressure. Logging each attempt
with category, latency and tokens is what made it obvious during this project that the key had a **20 requests/day
free-tier quota**. The quota ID was in the 429 details.

**F3. Which failures are retried?**
Transient ones: 429 rate limit (honouring Gemini's `retryDelay`), 5xx, timeouts, network errors, with exponential backoff
and jitter. Invalid JSON or schema mismatches get exactly one retry. Permanent ones are never retried: 400, 401/403, 404
model not found, safety blocks, `MAX_TOKENS` truncation, and a *daily* quota exhaustion (`quota_exhausted`). The daily
quota case was added after seeing the system waste four retries on a 429 that would not clear for 11 hours.

**F4. How do you prevent infinite retry loops?**
Bounded at two levels: `MODEL_MAX_ATTEMPTS` per extraction, and `MAX_JOB_ATTEMPTS` per receipt for crash or lease loops.
There is also a daily cap on model calls (`DAILY_MODEL_CALL_LIMIT`) as a wallet guard.

**F5. How do you track latency and tokens?**
Latency is wall-clock around each provider call. Tokens come from `usage_metadata`: prompt, candidates and thinking
tokens. If the API omits a value, the column is null; nothing is estimated. `/api/model-calls` reports p50/p95 latency and
averages, and so does the evaluation report. Cost is **not** computed: there is no price table, which is a known gap
against the roadmap.

**F6. How would you debug a failed extraction?**
Open the receipt and check `error_code`. The History tab shows every attempt with its category and the trace ID. Then
check `extraction_history` for a schema-invalid version: the raw text and the first 20 schema errors are stored.
Re-extract once the cause is fixed. For a wrong value that passed every check, use the `--replay` evaluation workflow to
test a new rule against stored outputs without new model calls.

## G. Evaluation

**G1. Why is evaluation essential?**
A demo proves it worked once. Changing a prompt, model or rule can silently break other receipts, and only a fixed
labelled set tells you. The roadmap's rule is "no eval, not done".

**G2. How is the dataset built?**
`evaluation/generate_dataset.py` renders 50 fictional receipts from specs whose values are the ground truth, plus 3 exact
duplicate files and 3 re-scans. Coverage: 12 currencies including 0- and 3-decimal ones, 7 date formats including
ambiguous ones, tax-inclusive VAT, discounts, service charges, tips, fractional quantities, blur, rotation, noise, crop and
smudge, missing fields, planted wrong totals, a refund, a non-receipt, two-page PDFs, and near-miss non-duplicates. Labels
describe what is *visible*: a cropped-off total is `null`.

**G3. Field accuracy vs exact-match receipt accuracy?**
Field accuracy scores each field independently (e.g. 95% of totals correct). Exact match requires *all* six key fields
**and** every line item to be right on the same receipt. It is much stricter and closer to "would this receipt need no
fix?". The report gives both, plus line-item precision, recall and F1.

**G4. How do you measure uncertainty detection?**
With the roadmap's two numbers. *Flag recall*: of the key-field values the model got wrong, how many did the system flag?
*Review load*: of the values it got right, how many were flagged anyway? Plus receipt-level precision and recall of
"needs review", and whether the deliberately hard fields were flagged.

**G5. How do you measure duplicate detection?**
Documents are processed in manifest order. Exact duplicates are detected by hash; probable ones by running
`find_duplicates` against earlier predictions. Precision and recall are reported separately for exact and probable, using
the labelled `duplicate_kind`. Near-miss negatives (same merchant and date with a different total, and so on) exist
specifically to measure precision.

**G6. How do you avoid data leakage?**
The prompt contains no examples from the evaluation set, and no rule was written by looking at a specific case's expected
output. The validators are generic accounting rules. The honest caveat: I designed both the dataset and the rules, so the
dataset reflects the failure modes I anticipated. Real receipts will have failure modes I did not anticipate. The corrections
log is the path to an evaluation set built from real use.

**G7. How do you know the scorer itself is right?**
`tests/test_eval_metrics.py` builds a perfect "oracle" reply from the labels and asserts 100% field accuracy, exact match and
line-item F1. It also asserts that the planted wrong total is caught and that a failed extraction counts as wrong. Those
tests check the harness, not the model.

**G8. What are the limitations of the results?**
The receipts are synthetic, rendered text: no handwriting, no crumpled thermal paper, only one font per layout. Numbers on
this set are an optimistic upper bound for clean printed receipts, not real-world photo accuracy. The set is small: 50
receipts means one receipt is 2 percentage points, so small differences between runs are noise. And because of quota,
the published run used a Flash-Lite model (see K).

## H. Security and deployment

**H1. Why must the key stay on the backend?**
Anything shipped to the browser is public. With the key, anyone could spend your quota or send arbitrary content under your
account. The frontend only knows `VITE_API_BASE_URL`.

**H2. How are uploads validated?**
By content, not name: magic-byte sniffing, full decode with Pillow, a decompression-bomb limit, a size limit (also checked
from `Content-Length` before reading), a PDF page limit, and encrypted PDFs rejected. A per-IP rate limit and a daily
model-call cap guard cost.

**H3. What changes between development and production?**
`APP_ENV=production` disables `/docs` and switches to JSON logs. CORS is pinned to the real origin; `STORAGE_BACKEND=database`;
migrations run at boot (`start.sh`); the container runs as a non-root user; the frontend gets the real API URL at build time.

**H4. Why might uploaded files disappear after a redeploy?**
Platform disks like Render's are ephemeral, so anything written locally is lost on deploy or restart. That is why
originals are stored in Postgres by default.

**H5. How are migrations applied?**
Alembic (`backend/migrations`). The initial migration was autogenerated and then fixed by hand: the circular
receipts↔extractions foreign key had to be added after both tables existed. `alembic check` confirms models and migrations
match. The production container runs `alembic upgrade head` before starting, and it was tested against an empty database.

**H6. How would you protect financial information properly?**
What exists: no secrets in the client, metadata-only logs, EXIF stripping, safe file serving, CSV formula-injection
guarding, and an optional shared access code. What is missing for real users: authentication with per-user data isolation
(`user_id` on every row and unique per user), encryption at rest for stored files, retention and deletion policies, audit
of who approved what, and a paid Gemini tier whose terms do not use inputs for training.

## I. System design and next steps

**I1. Thousands of receipts a day?**
Move the worker to its own process or service and scale it horizontally (SKIP LOCKED already makes that safe). Move
files to object storage. Split the long transaction around the model call. Add per-user quotas, use a queue such as
Redis/RQ or Cloud Tasks if Postgres polling becomes a bottleneck, and use the Gemini Batch API for non-urgent work.

**I2. When would you introduce a dedicated job queue?**
When polling load or latency matters, or when jobs need scheduling, priorities, or fan-out. The Postgres table queue is
deliberately simple: one fewer service and transactional with the data. Its limit is polling and connection usage at scale.

**I3. How would you handle rate limits?**
Already: backoff honouring `retryDelay`, a fast fail on daily quota, and a daily cap. Next: a token-bucket limiter shared
across workers (in Postgres or Redis), a fallback model on `quota_exhausted`, and paid-tier quotas for production.

**I4. Authentication and multi-user?**
Add an auth provider (OAuth/OIDC). Put `user_id` on receipts and expenses and filter every query by it. Change
`UNIQUE(sha256)` to `UNIQUE(user_id, sha256)`, which is what the roadmap specifies. The current single-tenant design is a
limitation for a public deployment, so the access code exists only to keep a demo from being open.

**I5. Multiple currencies and exchange rates?**
Amounts are already stored per currency with the correct exponent, and totals are grouped by currency, never summed across
currencies. Conversion would need a rates table keyed by date, conversion at the expense date, and storage of both original
and converted amounts. Not implemented.

**I6. How would you improve extraction accuracy?**
Use the evaluation's error table to find the dominant failure mode first. Candidates: two-pass consensus (built, off by
default because it doubles cost), a stronger model for documents flagged as partially legible, an OCR second opinion,
locale hints for date order (merchant country → DD/MM vs MM/DD), and few-shot examples drawn from the corrections log.
Each change gets an evaluation run before and after.

**I7. How would you halve cost per receipt?**
Use a smaller model for clean, single-page receipts and escalate only flagged ones. Downscale images further (tokens scale
with pixels). Use low or minimal thinking. Skip the second pass unless a rule fails. Use batch pricing for backfills.
Measure each change on the evaluation set, because cheaper is only better if flag recall holds.

## J. Critical follow-ups

**J1. What is the weakest part?**
No real authentication: anyone with the URL (or the shared code) sees all receipts. Second: the evaluation set is
synthetic, so it does not prove real-world accuracy.

**J2. What failed during development?**
(1) The amount parser silently turned `1O.5O` into `1.5`; a test caught it, and it now raises. (2) The first evaluation run
used `gemini-3.5-flash` and hit the key's 20-requests-per-day free-tier quota halfway through. The runner wrote results only
at the end, so the paid-for outputs were lost. Fixes: append outputs incrementally, add `--resume`, classify daily-quota 429s
as non-retryable, and finish the run on another model. (3) The autogenerated migration placed a circular foreign key inline,
which fails on Postgres. (4) `gemini-3.8-flash` returned 503 "high demand", so I moved to `gemini-3.5-flash`; after its free-tier daily quota ran out, the evaluation and the default became `gemini-3.5-flash-lite`.

**J3. Which design decision would you change?**
Running the model call inside the pipeline's database transaction. It is simple and atomic, but it holds a connection for the
whole call. I would claim, call, then write in a short transaction.

**J4. What did the evaluation actually prove?**
That on this labelled synthetic set, with the stated model and prompt version, the pipeline achieved the reported field
accuracy, and that the validators caught the planted arithmetic errors at the reported rate with the measured flag recall
and review load. It does not prove accuracy on real photos, on other models, or at scale.

**J5. What would you build next?**
Authentication and per-user isolation; a separate worker service; object storage; an export of corrections into new
evaluation cases; a cost table from token counts; and a small real-receipt evaluation set (with consent) next to the
synthetic one.

## K. Numbers to know

Always quote them with their context: *56 synthetic documents, `gemini-3.5-flash-lite`, prompt `receipt-extract-v1`*.

| Number | Value | What it does and does not mean |
|---|---|---|
| Schema-valid responses | 53/53 (100%) | Shape only. Says nothing about correctness. |
| Key-field accuracy | 311/312 (99.7%) | Clean synthetic print. Not a real-photo claim. |
| Exact-match receipts | 51/52 (98.1%) | The one miss: ambiguous `03/06/2026` read as `2026-03-03`. It *was* flagged. |
| Live flag recall | 1/1 | **n = 1, meaningless as a rate.** Use the perturbation test instead. |
| Perturbation test | 228/315 injected misreads flagged | Totals 100%, subtotals 100%, line totals 98%, tax 68% (misses on tax-inclusive receipts), plausible date/currency swaps 0%. |
| Planted arithmetic errors | 3/3 caught, 0/47 false alarms | Only 3 planted cases, so the report labels it a small sample. |
| Review load | 27/311 (8.7%) | Mostly deliberate: ambiguous dates the dataset rule requires to be confirmed. |
| Receipt review detection | 96.0% precision, 100% recall | The false positive is r025, where the model mis-assigned a line quantity and the line check correctly fired. |
| Duplicates | 100% precision and recall | Only 3 exact + 3 re-scans + 3 near-miss negatives. |
| Latency | p50 3.7 s, p95 5.0 s | Paced single-worker run on the free tier. Excludes retry backoff. |
| Tokens | ~1,571 in / ~773 out per receipt | From `usage_metadata`. No cost computed. |

*Challenge:* "Your accuracy is 99.7%, so why have review at all?" → Because the set is synthetic, and the perturbation test
shows which real misreads the rules would miss (a plausible date, a different valid currency, VAT on tax-inclusive
receipts). Review is the control for exactly those cases.
