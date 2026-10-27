import type {
  Dashboard, ExpenseList, Health, LineItemInput, ModelCallList, Paged, ReceiptDetail, ReceiptSummary, UploadResult,
} from "./types";

/**
 * Base URL of the FastAPI backend. Empty in development (Vite proxies /api). In production set
 * VITE_API_BASE_URL to the deployed API origin. Only public values belong in VITE_* variables:
 * the Gemini key lives on the server and never reaches the browser.
 */
export const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
const ACCESS_KEY = "expense-scanner.access-code";

export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown> | null;

  constructor(status: number, code: string, message: string, details: Record<string, unknown> | null = null) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export function getAccessCode(): string | null {
  try {
    return localStorage.getItem(ACCESS_KEY);
  } catch {
    return null;
  }
}

export function setAccessCode(code: string | null) {
  try {
    if (code) localStorage.setItem(ACCESS_KEY, code);
    else localStorage.removeItem(ACCESS_KEY);
  } catch {
    /* storage unavailable: the code is simply not remembered */
  }
}

function headers(extra?: HeadersInit): Headers {
  const h = new Headers(extra);
  const code = getAccessCode();
  if (code) h.set("X-Access-Code", code);
  return h;
}

async function toError(res: Response): Promise<ApiError> {
  try {
    const body = await res.json();
    const err = body?.error;
    if (err?.code) return new ApiError(res.status, err.code, err.message ?? "Request failed.", err.details ?? null);
  } catch {
    /* non-JSON error body */
  }
  const fallback = res.status >= 500 ? "The server had a problem. Try again shortly." : `Request failed (${res.status}).`;
  return new ApiError(res.status, "http_error", fallback);
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { ...init, headers: headers(init.headers) });
  } catch {
    throw new ApiError(0, "network_error", "Cannot reach the server. Check your connection or the API URL.");
  }
  if (!res.ok) throw await toError(res);
  return (await res.json()) as T;
}

function json(method: string, body: unknown): RequestInit {
  return { method, body: JSON.stringify(body), headers: { "Content-Type": "application/json" } };
}

export interface ReceiptQuery {
  q?: string;
  merchant?: string;
  status?: string;
  date_from?: string;
  date_to?: string;
  duplicates_only?: boolean;
  limit?: number;
  offset?: number;
}

function qs(params: object): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "" || value === false) continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

/** Upload with progress (fetch has no upload progress events, so this uses XMLHttpRequest). */
export function uploadReceipt(file: File, idempotencyKey: string, onProgress?: (fraction: number) => void): Promise<UploadResult> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/api/receipts`);
    const code = getAccessCode();
    if (code) xhr.setRequestHeader("X-Access-Code", code);
    // One key per selected file: a double-submit or retry of the same selection never creates two receipts.
    xhr.setRequestHeader("Idempotency-Key", idempotencyKey);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && onProgress) onProgress(event.loaded / event.total);
    };
    xhr.onerror = () => reject(new ApiError(0, "network_error", "Upload failed: the server could not be reached."));
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* handled below */
      }
      if (xhr.status >= 200 && xhr.status < 300 && body) return resolve(body as UploadResult);
      const err = (body as { error?: { code: string; message: string; details?: Record<string, unknown> } } | null)?.error;
      reject(new ApiError(xhr.status, err?.code ?? "upload_failed", err?.message ?? `Upload failed (${xhr.status}).`));
    };
    const form = new FormData();
    form.append("file", file);
    xhr.send(form);
  });
}

async function fetchBlob(path: string): Promise<Blob> {
  const res = await fetch(`${API_BASE}${path}`, { headers: headers() });
  if (!res.ok) throw await toError(res);
  return res.blob();
}

export const api = {
  health: () => request<Health>("/api/health"),
  dashboard: () => request<Dashboard>("/api/dashboard"),
  receipts: (query: ReceiptQuery) => request<Paged<ReceiptSummary>>(`/api/receipts${qs(query)}`),
  reviewQueue: () => request<Paged<ReceiptSummary>>("/api/review-queue"),
  receipt: (id: string) => request<ReceiptDetail>(`/api/receipts/${id}`),
  receiptFile: (id: string) => fetchBlob(`/api/receipts/${id}/file`),
  correctField: (id: string, name: string, value: string | null, note?: string) =>
    request<ReceiptDetail>(`/api/receipts/${id}/fields/${name}`, json("PATCH", { value, note })),
  confirmField: (id: string, name: string) =>
    request<ReceiptDetail>(`/api/receipts/${id}/fields/${name}/confirm`, { method: "POST" }),
  replaceLineItems: (id: string, items: LineItemInput[], note?: string) =>
    request<ReceiptDetail>(`/api/receipts/${id}/line-items`, json("PUT", { items, note })),
  resolveDuplicate: (id: string, resolution: "duplicate" | "not_duplicate", note?: string) =>
    request<ReceiptDetail>(`/api/receipts/${id}/duplicate`, json("POST", { resolution, note })),
  approve: (id: string, note?: string) => request<ReceiptDetail>(`/api/receipts/${id}/approve`, json("POST", { note })),
  reject: (id: string, note?: string) => request<ReceiptDetail>(`/api/receipts/${id}/reject`, json("POST", { note })),
  reprocess: (id: string) => request<ReceiptDetail>(`/api/receipts/${id}/reprocess`, json("POST", {})),
  expenses: (month?: string, category?: string) => request<ExpenseList>(`/api/expenses${qs({ month, category })}`),
  exportExpenses: (month?: string, category?: string) => fetchBlob(`/api/expenses/export.csv${qs({ month, category })}`),
  modelCalls: (limit = 50) => request<ModelCallList>(`/api/model-calls?limit=${limit}`),
};
