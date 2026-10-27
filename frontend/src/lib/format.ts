import type { FieldStatus, ReceiptStatus } from "./types";

const ZERO_DECIMAL = new Set("BIF CLP DJF GNF ISK JPY KMF KRW PYG RWF UGX VND VUV XAF XOF XPF".split(" "));
const THREE_DECIMAL = new Set("BHD IQD JOD KWD LYD OMR TND".split(" "));

export function currencyExponent(currency: string | null | undefined): number {
  if (!currency) return 2;
  if (ZERO_DECIMAL.has(currency)) return 0;
  if (THREE_DECIMAL.has(currency)) return 3;
  return 2;
}

/** Format integer minor units. Uses string arithmetic so no float rounding touches the value. */
export function formatMinor(minor: number | null | undefined, currency: string | null | undefined): string {
  if (minor === null || minor === undefined) return "—";
  const exp = currencyExponent(currency);
  const negative = minor < 0;
  const digits = String(Math.abs(minor)).padStart(exp + 1, "0");
  const whole = digits.slice(0, digits.length - exp) || "0";
  const frac = exp ? digits.slice(-exp) : "";
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const amount = `${negative ? "−" : ""}${grouped}${frac ? `.${frac}` : ""}`;
  return currency ? `${currency} ${amount}` : amount;
}

/** Format a decimal string from the API ("12.5") to the currency's precision without floats. */
export function formatDecimal(value: string | null | undefined, currency: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const exp = currencyExponent(currency);
  const match = /^(-)?(\d+)(?:\.(\d+))?$/.exec(value.trim());
  if (!match) return value;
  const [, sign, whole = "0", frac = ""] = match;
  const padded = exp ? (frac + "0".repeat(exp)).slice(0, Math.max(exp, frac.replace(/0+$/, "").length)) : frac.replace(/0+$/, "");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${sign ? "−" : ""}${grouped}${padded ? `.${padded}` : ""}`;
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  if (!y || !m || !d) return iso;
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" });
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function relativeTime(iso: string, now: number = Date.now()): string {
  const seconds = Math.round((now - new Date(iso).getTime()) / 1000);
  if (seconds < 45) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days} d ago`;
  return formatDateTime(iso);
}

export function formatMonth(month: string): string {
  const [y, m] = month.split("-").map(Number);
  if (!y || !m) return month;
  return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString(undefined, { month: "short", year: "numeric", timeZone: "UTC" });
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`;
}

export const RECEIPT_STATUS_LABEL: Record<ReceiptStatus, string> = {
  queued: "Queued",
  processing: "Extracting",
  needs_review: "Needs review",
  approved: "Approved",
  rejected: "Rejected",
  failed: "Failed",
};

export const FIELD_STATUS_LABEL: Record<FieldStatus, string> = {
  extracted: "Unreviewed",
  uncertain: "Doubtful",
  invalid: "Invalid",
  missing: "Missing",
  verified: "Verified",
};

export function titleCase(text: string): string {
  return text.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

export function isInFlight(status: ReceiptStatus): boolean {
  return status === "queued" || status === "processing";
}
