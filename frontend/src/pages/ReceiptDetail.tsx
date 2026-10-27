import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, CheckCircle2, Copy, Loader2, RotateCcw, TriangleAlert, XCircle } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router";
import DocumentPreview from "../components/DocumentPreview";
import Checks from "../components/review/Checks";
import FieldsTable from "../components/review/FieldsTable";
import History from "../components/review/History";
import LineItems from "../components/review/LineItems";
import { useToast } from "../components/toast";
import { Button, Dialog, ErrorBox, Panel, Spinner, StatusBadge, cx } from "../components/ui";
import { ApiError, api } from "../lib/api";
import { formatBytes, formatDate, formatDateTime, formatMinor, formatMs, isInFlight, titleCase } from "../lib/format";
import type { LineItemInput, ReceiptDetail as Detail } from "../lib/types";

type DialogKind = "approve" | "reject" | "reprocess" | null;

function NoteField({ value, onChange, required, label }: { value: string; onChange: (v: string) => void; required: boolean; label: string }) {
  return (
    <div className="mt-3">
      <label htmlFor="decision-note" className="mb-1 block text-xs font-medium text-ink-2">{label}{required ? " (required)" : " (optional)"}</label>
      <textarea id="decision-note" rows={3} value={value} onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-md border border-line-strong px-2.5 py-2 text-sm focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20" />
    </div>
  );
}

export default function ReceiptDetail() {
  const { id = "" } = useParams();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [dialog, setDialog] = useState<DialogKind>(null);
  const [note, setNote] = useState("");
  const [dialogError, setDialogError] = useState<ApiError | null>(null);
  const [busyField, setBusyField] = useState<string | null>(null);
  const [tab, setTab] = useState<"checks" | "history">("checks");

  const query = useQuery({
    queryKey: ["receipt", id],
    queryFn: () => api.receipt(id),
    refetchInterval: (q) => (q.state.data && isInFlight(q.state.data.receipt.status) ? 2000 : false),
  });

  function applied(detail: Detail, message?: string) {
    queryClient.setQueryData(["receipt", id], detail);
    for (const key of ["receipts", "review-queue", "dashboard", "expenses"]) queryClient.invalidateQueries({ queryKey: [key] });
    if (message) toast.success(message);
  }

  const action = useMutation({
    mutationFn: async (kind: Exclude<DialogKind, null>) => {
      if (kind === "approve") return api.approve(id, note.trim() || undefined);
      if (kind === "reject") return api.reject(id, note.trim() || undefined);
      return api.reprocess(id);
    },
    onSuccess: (detail, kind) => {
      setDialog(null);
      setNote("");
      applied(detail, kind === "approve" ? "Approved and added to the expense tracker." : kind === "reject" ? "Receipt rejected." : "Queued for a new extraction.");
    },
    onError: (error) => setDialogError(error instanceof ApiError ? error : new ApiError(0, "error", "Action failed.")),
  });

  const duplicate = useMutation({
    mutationFn: (resolution: "duplicate" | "not_duplicate") => api.resolveDuplicate(id, resolution),
    onSuccess: (detail, resolution) => applied(detail, resolution === "duplicate" ? "Marked as a duplicate and rejected." : "Duplicate warning dismissed."),
    onError: (e) => toast.error(e instanceof Error ? e.message : "Could not resolve the duplicate."),
  });

  const lines = useMutation({
    mutationFn: (rows: LineItemInput[]) => api.replaceLineItems(id, rows),
    onSuccess: (detail) => applied(detail, "Line items saved and re-validated."),
    onError: (e) => toast.error(e instanceof Error ? e.message : "Could not save line items."),
  });

  async function saveField(name: string, value: string | null): Promise<boolean> {
    setBusyField(name);
    try {
      applied(await api.correctField(id, name, value), "Correction saved and re-validated.");
      return true;
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not save the correction.");
      return false;
    } finally {
      setBusyField(null);
    }
  }

  async function confirmField(name: string) {
    setBusyField(name);
    try {
      applied(await api.confirmField(id, name));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not confirm the field.");
    } finally {
      setBusyField(null);
    }
  }

  function open(kind: Exclude<DialogKind, null>) {
    setDialogError(null);
    setNote("");
    setDialog(kind);
  }

  if (query.error) {
    return (
      <>
        <Link to="/receipts" className="mb-4 inline-flex items-center gap-1 text-[13px] text-ink-2 hover:text-ink"><ArrowLeft className="size-4" /> Receipts</Link>
        <ErrorBox error={query.error} onRetry={() => query.refetch()} />
      </>
    );
  }
  if (!query.data) return <Spinner label="Loading receipt" />;

  const d = query.data;
  const r = d.receipt;
  const can = (a: string) => d.allowed_actions.includes(a as never);
  const failing = d.checks.filter((c) => !c.passed && c.severity !== "info");
  const blocking = (dialogError?.details?.blocking as string[] | undefined) ?? [];

  return (
    <>
      <Link to="/receipts" className="mb-3 inline-flex items-center gap-1 text-[13px] text-ink-2 hover:text-ink"><ArrowLeft className="size-4" /> Receipts</Link>

      <div className="mb-5 flex flex-col gap-3 border-b border-line pb-5 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="truncate text-xl font-semibold tracking-tight">{r.merchant ?? r.original_filename}</h1>
            <StatusBadge status={r.status} approvedBy={r.approved_by} />
          </div>
          <p className="num mt-1 text-ink-2">
            {formatDate(r.receipt_date)} · <span className="font-medium text-ink">{formatMinor(r.total_minor, r.currency)}</span>
            {d.expense && <> · {titleCase(d.expense.category)}</>}
          </p>
          <p className="mt-1 text-xs text-ink-3">
            Uploaded {formatDateTime(r.created_at)} · {formatBytes(d.size_bytes)}{r.page_count && r.page_count > 1 ? ` · ${r.page_count} pages` : ""}
            {d.extraction && <> · {d.extraction.model} · {d.extraction.prompt_version} · v{d.extraction.version} · {formatMs(d.extraction.latency_ms)}</>}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          {can("reprocess") && <Button icon={<RotateCcw className="size-4" />} onClick={() => open("reprocess")}>Re-extract</Button>}
          {can("reject") && <Button variant="danger" icon={<XCircle className="size-4" />} onClick={() => open("reject")}>Reject</Button>}
          {can("approve") && <Button variant="primary" icon={<CheckCircle2 className="size-4" />} onClick={() => open("approve")}>Approve</Button>}
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <div className="lg:sticky lg:top-6 lg:self-start">
          <DocumentPreview receiptId={r.id} contentType={r.content_type} filename={r.original_filename} />
        </div>

        <div className="min-w-0 space-y-5">
          {isInFlight(r.status) && (
            <div className="flex items-center gap-3 rounded-md border border-info/20 bg-info-soft px-4 py-3 text-info">
              <Loader2 className="size-4 animate-spin" aria-hidden />
              <p>{r.status === "queued" ? "Waiting for the extraction worker…" : "Gemini is reading the document. This usually takes a few seconds."}</p>
            </div>
          )}

          {r.status === "failed" && (
            <div role="alert" className="rounded-md border border-bad/30 bg-bad-soft px-4 py-3">
              <p className="font-medium text-bad">Extraction failed{r.error_code ? ` · ${r.error_code}` : ""}</p>
              <p className="mt-0.5 text-[13px] text-ink">{r.error_message}</p>
              {d.extraction_history.some((e) => !e.schema_valid) && (
                <p className="mt-1 text-[13px] text-ink-2">The model's reply did not pass schema validation, so nothing from it was saved as receipt data.</p>
              )}
            </div>
          )}

          {r.status === "approved" && (
            <div className="flex items-start gap-2 rounded-md border border-ok/25 bg-ok-soft px-4 py-3 text-[13px]">
              <CheckCircle2 className="mt-0.5 size-4 text-ok" aria-hidden />
              <p>{r.approved_by === "auto_rules"
                ? "Approved automatically because no field was missing, invalid or doubtful. Values were not reviewed by a person."
                : "Approved by a reviewer. These values are in the expense tracker."}</p>
            </div>
          )}

          {d.duplicate.status === "probable" && d.duplicate.of && (
            <div className="rounded-md border border-warn/30 bg-warn-soft px-4 py-3">
              <p className="flex items-center gap-2 font-medium text-warn"><Copy className="size-4" /> Possible duplicate</p>
              <p className="mt-1 text-[13px]">
                Matches <Link to={`/receipts/${d.duplicate.of.id}`} className="font-medium text-accent hover:underline">
                  {d.duplicate.of.merchant ?? d.duplicate.of.original_filename}</Link> ({formatDate(d.duplicate.of.receipt_date)},{" "}
                {formatMinor(d.duplicate.of.total_minor, d.duplicate.of.currency)}, {titleCase(d.duplicate.of.status)}).
              </p>
              <ul className="mt-1 list-disc pl-5 text-[13px] text-ink-2">{d.duplicate.reasons.map((x) => <li key={x}>{x}</li>)}</ul>
              {can("resolve_duplicate") && (
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button size="sm" loading={duplicate.isPending && duplicate.variables === "not_duplicate"} onClick={() => duplicate.mutate("not_duplicate")}>It's a separate expense</Button>
                  <Button size="sm" variant="danger" loading={duplicate.isPending && duplicate.variables === "duplicate"} onClick={() => duplicate.mutate("duplicate")}>It's a duplicate — reject</Button>
                </div>
              )}
            </div>
          )}
          {d.duplicate.status === "dismissed" && <p className="text-[13px] text-ink-2">A duplicate warning was reviewed and dismissed.</p>}

          {r.status === "needs_review" && (
            d.review_reasons.length > 0 ? (
              <div className="rounded-md border border-warn/30 bg-surface">
                <p className="flex items-center gap-2 border-b border-warn/20 bg-warn-soft px-4 py-2 font-medium text-warn">
                  <TriangleAlert className="size-4" aria-hidden />
                  {d.review_reasons.length} thing{d.review_reasons.length === 1 ? "" : "s"} to check before approving
                </p>
                <ul className="divide-y divide-line">
                  {d.review_reasons.map((reason, i) => (
                    <li key={i} className="px-4 py-2 text-[13px]">{reason.message}</li>
                  ))}
                </ul>
              </div>
            ) : (
              <p className="rounded-md border border-line bg-surface px-4 py-3 text-[13px] text-ink-2">
                No field was missing, invalid or doubtful. Compare the values with the document, then approve.
              </p>
            )
          )}

          {d.extraction && (
            <>
              <Panel title="Extracted fields" bodyClassName=""
                actions={<span className="text-xs text-ink-3">{d.extraction.readability ? `Readability: ${titleCase(d.extraction.readability)}` : null}</span>}>
                <FieldsTable fields={d.fields} currency={r.currency} editable={can("edit")} busyField={busyField} onSave={saveField} onConfirm={confirmField} />
              </Panel>
              <Panel title={`Line items (${d.line_items.length})`} bodyClassName="">
                <LineItems items={d.line_items} currency={r.currency} editable={can("edit")} saving={lines.isPending}
                  onSave={async (rows) => { try { await lines.mutateAsync(rows); return true; } catch { return false; } }} />
              </Panel>
              {d.extraction.notes.length > 0 && (
                <Panel title="Model notes">
                  <ul className="list-disc space-y-1 pl-5 text-[13px] text-ink-2">{d.extraction.notes.map((n) => <li key={n}>{n}</li>)}</ul>
                </Panel>
              )}
            </>
          )}

          <Panel bodyClassName="p-4"
            title={
              <div className="flex gap-4" role="tablist">
                {(["checks", "history"] as const).map((t) => (
                  <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
                    className={cx("-mb-[9px] border-b-2 pb-2", tab === t ? "border-accent text-ink" : "border-transparent text-ink-2 hover:text-ink")}>
                    {t === "checks" ? `Validation (${failing.length} failing)` : "History"}
                  </button>
                ))}
              </div>
            }>
            {tab === "checks" ? (d.checks.length ? <Checks checks={d.checks} /> : <p className="text-[13px] text-ink-2">Checks run after extraction.</p>) : <History detail={d} />}
          </Panel>
        </div>
      </div>

      <Dialog open={dialog === "approve"} title="Approve receipt" onClose={() => setDialog(null)}
        footer={<>
          <Button variant="ghost" onClick={() => setDialog(null)}>Cancel</Button>
          <Button variant="primary" loading={action.isPending} disabled={failing.length > 0 && !note.trim()} onClick={() => action.mutate("approve")}>Approve</Button>
        </>}>
        <p className="text-[13px] text-ink-2">
          Adds <span className="num font-medium text-ink">{formatMinor(r.total_minor, r.currency)}</span> from {r.merchant ?? "this merchant"} on {formatDate(r.receipt_date)} to the expense tracker and marks the values as verified by you.
        </p>
        {failing.length > 0 && (
          <div className="mt-3 rounded-md border border-warn/30 bg-warn-soft px-3 py-2 text-[13px]">
            <p className="font-medium text-warn">{failing.length} check{failing.length === 1 ? "" : "s"} still failing</p>
            <ul className="mt-1 list-disc pl-5 text-ink-2">{failing.map((c, i) => <li key={i}>{c.message}</li>)}</ul>
          </div>
        )}
        <NoteField value={note} onChange={setNote} required={failing.length > 0}
          label={failing.length ? "Why are these values correct as entered?" : "Note"} />
        {dialogError && (
          <div role="alert" className="mt-3 text-[13px] text-bad">
            <p>{dialogError.message}</p>
            {blocking.length > 0 && <ul className="mt-1 list-disc pl-5">{blocking.map((b) => <li key={b}>{b}</li>)}</ul>}
          </div>
        )}
      </Dialog>

      <Dialog open={dialog === "reject"} title="Reject receipt" onClose={() => setDialog(null)}
        footer={<>
          <Button variant="ghost" onClick={() => setDialog(null)}>Cancel</Button>
          <Button variant="danger" loading={action.isPending} onClick={() => action.mutate("reject")}>Reject</Button>
        </>}>
        <p className="text-[13px] text-ink-2">The receipt stays in history but is never added to the expense tracker.</p>
        <NoteField value={note} onChange={setNote} required={false} label="Reason" />
        {dialogError && <p role="alert" className="mt-3 text-[13px] text-bad">{dialogError.message}</p>}
      </Dialog>

      <Dialog open={dialog === "reprocess"} title="Run extraction again?" onClose={() => setDialog(null)}
        footer={<>
          <Button variant="ghost" onClick={() => setDialog(null)}>Cancel</Button>
          <Button variant="primary" loading={action.isPending} onClick={() => action.mutate("reprocess")}>Re-extract</Button>
        </>}>
        <p className="text-[13px] text-ink-2">
          Sends the original document to the model again and creates a new extraction version. Earlier versions and your corrections are kept in history,
          but the new version starts unreviewed. This uses one more model call.
        </p>
        {dialogError && <p role="alert" className="mt-3 text-[13px] text-bad">{dialogError.message}</p>}
      </Dialog>
    </>
  );
}
