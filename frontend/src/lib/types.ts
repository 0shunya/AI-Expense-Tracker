export type ReceiptStatus = "queued" | "processing" | "needs_review" | "approved" | "rejected" | "failed";
export type FieldStatus = "extracted" | "uncertain" | "invalid" | "missing" | "verified";
export type DuplicateStatus = "none" | "probable" | "dismissed" | "confirmed";

export interface ReceiptSummary {
  id: string;
  original_filename: string;
  content_type: string;
  page_count: number | null;
  status: ReceiptStatus;
  merchant: string | null;
  receipt_date: string | null;
  currency: string | null;
  total_minor: number | null;
  total: string | null;
  review_reason_count: number;
  duplicate_status: DuplicateStatus;
  approved_by: string | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface ReceiptField {
  name: string;
  label: string;
  required: boolean;
  money: boolean;
  value: string | null;
  extracted_value: string | null;
  source_text: string | null;
  status: FieldStatus;
  reason: string | null;
}

export interface LineItem {
  position: number;
  description: string | null;
  quantity: string | null;
  unit_price: string | null;
  line_total: string | null;
  discount: string | null;
  tax: string | null;
  source_text: string | null;
  status: FieldStatus;
  reason: string | null;
}

export interface Check {
  code: string;
  severity: "error" | "warning" | "info";
  passed: boolean;
  fields: string[];
  message: string;
  details: Record<string, unknown> | null;
}

export interface ReviewReason {
  code: string;
  message: string;
  field: string | null;
}

export interface Extraction {
  id: string;
  version: number;
  provider: string;
  model: string;
  prompt_version: string;
  passes: number;
  schema_valid: boolean;
  readability: string | null;
  is_receipt: boolean | null;
  tax_included: boolean | null;
  category_suggestion: string | null;
  notes: string[];
  latency_ms: number | null;
  trace_id: string;
  created_at: string;
}

export interface Correction {
  field: string;
  action: "corrected" | "confirmed";
  old_value: unknown;
  new_value: unknown;
  note: string | null;
  created_at: string;
}

export interface Decision {
  decision: string;
  note: string | null;
  overridden_checks: { code: string; message: string }[] | null;
  created_at: string;
}

export interface ModelCall {
  trace_id: string;
  receipt_id: string | null;
  provider: string;
  model: string;
  prompt_version: string;
  attempt: number;
  status: "success" | "error";
  error_category: string | null;
  error_message: string | null;
  finish_reason: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  thinking_tokens: number | null;
  total_tokens: number | null;
  latency_ms: number;
  created_at: string;
}

export interface Expense {
  id: string;
  receipt_id: string;
  merchant: string;
  expense_date: string;
  currency: string;
  total_minor: number;
  total: string;
  tax_minor: number | null;
  kind: "purchase" | "refund";
  category: string;
  approved_by: string;
  created_at: string;
}

export type Action = "edit" | "approve" | "reject" | "reprocess" | "resolve_duplicate";

export interface ReceiptDetail {
  receipt: ReceiptSummary;
  size_bytes: number;
  sha256: string;
  review_reasons: ReviewReason[];
  duplicate: { status: DuplicateStatus; reasons: string[]; of: ReceiptSummary | null };
  extraction: Extraction | null;
  fields: ReceiptField[];
  line_items: LineItem[];
  checks: Check[];
  corrections: Correction[];
  decisions: Decision[];
  model_calls: ModelCall[];
  extraction_history: { version: number; model: string; prompt_version: string; schema_valid: boolean; created_at: string; current: boolean; schema_errors: { loc: string; msg: string }[] | null }[];
  expense: Expense | null;
  allowed_actions: Action[];
}

export interface Paged<T> {
  items: T[];
  total: number;
  limit?: number;
  offset?: number;
}

export interface UploadResult {
  receipt: ReceiptSummary;
  created: boolean;
  exact_duplicate: boolean;
  message: string;
}

export interface Health {
  status: string;
  version: string;
  database: string;
  environment: string;
  gemini_configured: boolean;
  model: string;
  prompt_version: string;
  storage: string;
  worker: string;
  access_code_required: boolean;
  max_upload_mb: number;
  max_pdf_pages: number;
}

export interface Dashboard {
  counts: Record<ReceiptStatus, number>;
  total_receipts: number;
  approved_spend: { currency: string; all_time_minor: number; this_month_minor: number; count: number }[];
  pending_review_totals: { currency: string; total_minor: number; count: number }[];
  monthly_approved: { month: string; currency: string; total_minor: number }[];
  recent_activity: Activity[];
}

export type Activity =
  | { kind: "decision"; decision: string; receipt_id: string; label: string; note: string | null; at: string }
  | { kind: "receipt"; status: ReceiptStatus; receipt_id: string; label: string; at: string };

export interface ExpenseList {
  items: Expense[];
  totals_by_category: { currency: string; category: string; total_minor: number }[];
  totals_by_currency: { currency: string; total_minor: number }[];
  available_months: string[];
}

export interface ModelCallList {
  window_days: number;
  stats: {
    calls: number;
    successes: number;
    failures: number;
    success_rate: number | null;
    latency_p50_ms: number | null;
    latency_p95_ms: number | null;
    avg_input_tokens: number | null;
    avg_output_tokens: number | null;
    calls_with_token_usage: number;
    errors_by_category: Record<string, number>;
  };
  items: ModelCall[];
}

export interface LineItemInput {
  description: string | null;
  quantity: string | null;
  unit_price: string | null;
  line_total: string | null;
  discount: string | null;
  tax: string | null;
}
