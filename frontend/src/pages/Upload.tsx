import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CircleAlert, CloudUpload, Copy, FileText, Loader2 } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import type { DragEvent } from "react";
import { Link } from "react-router";
import { Button, PageHeader, Panel, StatusBadge, cx } from "../components/ui";
import { ApiError, api, uploadReceipt } from "../lib/api";
import { formatBytes, formatMinor, isInFlight } from "../lib/format";
import { precheckFile } from "../lib/upload";

interface Item {
  key: string;
  file: File;
  state: "uploading" | "done" | "error";
  progress: number;
  error?: string;
  receiptId?: string;
  duplicate?: boolean;
}

function newKey() {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
}

function ProcessingStatus({ receiptId, duplicate }: { receiptId: string; duplicate?: boolean }) {
  const query = useQuery({
    queryKey: ["receipt", receiptId],
    queryFn: () => api.receipt(receiptId),
    refetchInterval: (q) => (q.state.data && !isInFlight(q.state.data.receipt.status) ? false : 2000),
  });
  const r = query.data?.receipt;
  if (!r) return <span className="text-xs text-ink-3">Checking status…</span>;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
      <StatusBadge status={r.status} approvedBy={r.approved_by} />
      {duplicate && <span className="inline-flex items-center gap-1 text-ink-2"><Copy className="size-3.5" />Same file already uploaded — no new extraction</span>}
      {r.status === "queued" && <span className="text-ink-2">Waiting for the extraction worker</span>}
      {r.status === "processing" && <span className="text-ink-2">Gemini is reading the document</span>}
      {r.status === "needs_review" && (
        <span className="text-ink-2">
          {r.merchant ?? "Unknown merchant"} · {formatMinor(r.total_minor, r.currency)} ·{" "}
          {r.review_reason_count ? `${r.review_reason_count} issue${r.review_reason_count === 1 ? "" : "s"} to check` : "no issues found"}
        </span>
      )}
      {r.status === "failed" && <span className="text-bad">{r.error_message}</span>}
      {!isInFlight(r.status) && <Link to={`/receipts/${r.id}`} className="font-medium text-accent hover:underline">
        {r.status === "needs_review" ? "Review" : "Open"} →</Link>}
    </div>
  );
}

export default function UploadPage() {
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const maxMb = health.data?.max_upload_mb ?? 10;
  const [items, setItems] = useState<Item[]>([]);
  const [rejections, setRejections] = useState<string[]>([]);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();

  const update = (key: string, patch: Partial<Item>) => setItems((all) => all.map((i) => (i.key === key ? { ...i, ...patch } : i)));

  const start = useCallback(async (item: Item) => {
    try {
      const result = await uploadReceipt(item.file, item.key, (p) => update(item.key, { progress: p }));
      update(item.key, { state: "done", progress: 1, receiptId: result.receipt.id, duplicate: result.exact_duplicate });
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    } catch (error) {
      const message = error instanceof ApiError ? error.message : "Upload failed.";
      update(item.key, { state: "error", error: message });
    }
  }, [queryClient]);

  function addFiles(files: FileList | File[]) {
    const accepted: Item[] = [];
    const problems: string[] = [];
    for (const file of Array.from(files)) {
      const problem = precheckFile(file, maxMb);
      if (problem) problems.push(problem);
      else accepted.push({ key: newKey(), file, state: "uploading", progress: 0 });
    }
    setRejections(problems);
    setItems((all) => [...accepted, ...all]);
    accepted.forEach((item) => void start(item));
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    if (event.dataTransfer.files.length) addFiles(event.dataTransfer.files);
  }

  return (
    <>
      <PageHeader title="Upload receipts"
        description="Photos or PDFs of receipts, bills and invoices. Files are checked by content, stored, and queued for extraction; the page updates as each one finishes." />

      {health.data && !health.data.gemini_configured && (
        <div className="mb-4 rounded-md border border-warn/30 bg-warn-soft px-4 py-3 text-[13px] text-ink">
          <span className="font-medium text-warn">Extraction is not configured.</span> Uploads will be stored, but extraction fails until
          GEMINI_API_KEY is set on the server.
        </div>
      )}

      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cx("flex flex-col items-center justify-center rounded-md border-2 border-dashed px-6 py-12 text-center transition-colors",
          dragging ? "border-accent bg-accent-soft" : "border-line-strong bg-surface")}
      >
        <CloudUpload className="mb-3 size-8 text-ink-3" aria-hidden />
        <p className="font-medium">Drag receipts here</p>
        <p className="mt-1 text-ink-2">JPEG, PNG, WebP or PDF · up to {maxMb} MB · PDFs up to {health.data?.max_pdf_pages ?? 5} pages</p>
        <Button variant="primary" className="mt-4" onClick={() => input.current?.click()}>Choose files</Button>
        <input ref={input} type="file" multiple accept="image/jpeg,image/png,image/webp,application/pdf,.jpg,.jpeg,.png,.webp,.pdf"
          className="sr-only" aria-label="Choose receipt files"
          onChange={(e) => { if (e.target.files?.length) addFiles(e.target.files); e.target.value = ""; }} />
      </div>

      {rejections.length > 0 && (
        <ul role="alert" className="mt-4 space-y-1 rounded-md border border-bad/30 bg-bad-soft px-4 py-3 text-[13px] text-bad">
          {rejections.map((r) => <li key={r}>{r}</li>)}
        </ul>
      )}

      {items.length > 0 && (
        <Panel title="This session" className="mt-6" bodyClassName="">
          <ul className="divide-y divide-line">
            {items.map((item) => (
              <li key={item.key} className="flex items-start gap-3 px-4 py-3">
                <FileText className="mt-0.5 size-4 shrink-0 text-ink-3" aria-hidden />
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{item.file.name} <span className="font-normal text-ink-3">· {formatBytes(item.file.size)}</span></p>
                  {item.state === "uploading" && (
                    <div className="mt-2 flex items-center gap-3">
                      <div className="h-1.5 flex-1 rounded-full bg-sunken" role="progressbar" aria-valuemin={0} aria-valuemax={100}
                        aria-valuenow={Math.round(item.progress * 100)} aria-label={`Uploading ${item.file.name}`}>
                        <div className="h-1.5 rounded-full bg-accent transition-[width]" style={{ width: `${item.progress * 100}%` }} />
                      </div>
                      <span className="num w-10 text-right text-xs text-ink-2">{Math.round(item.progress * 100)}%</span>
                    </div>
                  )}
                  {item.state === "error" && <p className="mt-1 text-[13px] text-bad">{item.error}</p>}
                  {item.state === "done" && item.receiptId && <div className="mt-1.5"><ProcessingStatus receiptId={item.receiptId} duplicate={item.duplicate} /></div>}
                </div>
                {item.state === "uploading" && <Loader2 className="size-4 animate-spin text-ink-3" aria-label="Uploading" />}
                {item.state === "done" && <CheckCircle2 className="size-4 text-ok" aria-label="Uploaded" />}
                {item.state === "error" && (
                  <div className="flex items-center gap-2">
                    <CircleAlert className="size-4 text-bad" aria-hidden />
                    <Button size="sm" onClick={() => { update(item.key, { state: "uploading", progress: 0, error: undefined }); void start(item); }}>Retry</Button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </>
  );
}
