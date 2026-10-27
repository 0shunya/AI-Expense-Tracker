import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Upload } from "lucide-react";
import { Link } from "react-router";
import ReceiptTable from "../components/ReceiptTable";
import { EmptyState, ErrorBox, LinkButton, PageHeader, Panel, Skeleton, Stat } from "../components/ui";
import { api } from "../lib/api";
import { formatMinor, formatMonth, formatMs, relativeTime, titleCase } from "../lib/format";
import type { Activity, Dashboard as DashboardData } from "../lib/types";

function MonthlyBars({ data }: { data: DashboardData["monthly_approved"] }) {
  const currencies = [...new Set(data.map((d) => d.currency))];
  if (!data.length) return <p className="text-ink-2">Approved expenses will be summarised here by month.</p>;
  return (
    <div className="space-y-5">
      {currencies.map((currency) => {
        const rows = data.filter((d) => d.currency === currency).slice(-6);
        const max = Math.max(...rows.map((r) => Math.abs(r.total_minor)), 1);
        return (
          <div key={currency}>
            {currencies.length > 1 && <p className="mb-2 text-xs font-medium text-ink-2">{currency}</p>}
            <ul className="space-y-1.5">
              {rows.map((r) => (
                <li key={r.month} className="grid grid-cols-[72px_1fr_auto] items-center gap-3 text-[13px]">
                  <span className="text-ink-2">{formatMonth(r.month)}</span>
                  <span className="h-2 rounded-sm bg-sunken">
                    <span className="block h-2 rounded-sm bg-accent" style={{ width: `${(Math.abs(r.total_minor) / max) * 100}%` }} />
                  </span>
                  <span className="num font-medium">{formatMinor(r.total_minor, currency)}</span>
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </div>
  );
}

const DECISION_TEXT: Record<string, string> = {
  approved: "approved", rejected: "rejected", duplicate_dismissed: "marked as not a duplicate",
  duplicate_confirmed: "confirmed as a duplicate", reprocess: "sent for re-extraction",
};

function ActivityItem({ item }: { item: Activity }) {
  const text = item.kind === "decision" ? `Reviewer ${DECISION_TEXT[item.decision] ?? item.decision}` : `Status: ${titleCase(item.status)}`;
  return (
    <li className="flex items-baseline justify-between gap-3 py-2">
      <div className="min-w-0">
        <Link to={`/receipts/${item.receipt_id}`} className="block truncate font-medium hover:text-accent">{item.label}</Link>
        <p className="text-xs text-ink-2">{text}</p>
      </div>
      <time className="shrink-0 text-xs text-ink-3" dateTime={item.at}>{relativeTime(item.at)}</time>
    </li>
  );
}

export default function Dashboard() {
  const dash = useQuery({ queryKey: ["dashboard"], queryFn: api.dashboard, refetchInterval: 10_000 });
  const recent = useQuery({ queryKey: ["receipts", { limit: 6 }], queryFn: () => api.receipts({ limit: 6 }), refetchInterval: 10_000 });
  const calls = useQuery({ queryKey: ["model-calls", 50], queryFn: () => api.modelCalls(50) });

  if (dash.error) return <ErrorBox error={dash.error} onRetry={() => dash.refetch()} />;
  const d = dash.data;
  const inFlight = d ? d.counts.queued + d.counts.processing : 0;

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Spending totals include approved receipts only. Unreviewed extractions are shown separately and never counted."
        actions={<LinkButton to="/upload" variant="primary" icon={<Upload className="size-4" />}>Upload receipts</LinkButton>}
      />

      <div className="grid grid-cols-2 divide-line rounded-md border border-line bg-surface md:grid-cols-4 md:divide-x [&>*:nth-child(-n+2)]:border-b [&>*:nth-child(-n+2)]:border-line md:[&>*]:border-b-0">
        {d ? (
          <>
            <Stat label="Receipts processed" value={d.total_receipts} hint={inFlight ? `${inFlight} extracting now` : "All extractions finished"} />
            <Stat label="Approved" value={d.counts.approved} tone="ok" hint={`${d.counts.rejected} rejected`} />
            <Stat label="Needs review" value={d.counts.needs_review} tone={d.counts.needs_review ? "warn" : undefined}
              hint={<Link to="/review" className="hover:text-accent">Open review queue →</Link>} />
            <Stat label="Failed extractions" value={d.counts.failed} tone={d.counts.failed ? "bad" : undefined}
              hint={d.counts.failed ? <Link to="/receipts?status=failed" className="hover:text-accent">See failures →</Link> : "None"} />
          </>
        ) : [0, 1, 2, 3].map((i) => <div key={i} className="px-4 py-3"><Skeleton className="h-3 w-24" /><Skeleton className="mt-2 h-7 w-12" /></div>)}
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <Panel title="Approved spending" className="lg:col-span-2"
          actions={<Link to="/expenses" className="text-[13px] text-accent hover:underline">Expense tracker</Link>}>
          {!d ? <Skeleton className="h-24" /> : d.approved_spend.length === 0 ? (
            <p className="text-ink-2">No approved expenses yet. Spending appears here only after a receipt is approved.</p>
          ) : (
            <div className="grid gap-6 md:grid-cols-[minmax(0,220px)_1fr]">
              <dl className="space-y-4">
                {d.approved_spend.map((s) => (
                  <div key={s.currency}>
                    <dt className="text-xs font-medium text-ink-2">{s.currency} · this month</dt>
                    <dd className="num text-2xl font-semibold tracking-tight">{formatMinor(s.this_month_minor, s.currency)}</dd>
                    <dd className="num text-xs text-ink-3">{formatMinor(s.all_time_minor, s.currency)} all time · {s.count} receipt{s.count === 1 ? "" : "s"}</dd>
                  </div>
                ))}
              </dl>
              <MonthlyBars data={d.monthly_approved} />
            </div>
          )}
          {d && d.pending_review_totals.length > 0 && (
            <div className="mt-5 rounded-md border border-dashed border-warn/40 bg-warn-soft/50 px-3 py-2 text-[13px] text-ink-2">
              <span className="font-medium text-warn">Not included:</span>{" "}
              {d.pending_review_totals.map((p) => `${formatMinor(p.total_minor, p.currency)} across ${p.count} unreviewed receipt${p.count === 1 ? "" : "s"}`).join("; ")}.
            </div>
          )}
        </Panel>

        <Panel title="Recent activity" bodyClassName="px-4 py-1">
          {!d ? <Skeleton className="my-3 h-24" /> : d.recent_activity.length === 0 ? (
            <p className="py-3 text-ink-2">Nothing yet.</p>
          ) : (
            <ul className="divide-y divide-line">{d.recent_activity.slice(0, 7).map((a, i) => <ActivityItem key={i} item={a} />)}</ul>
          )}
        </Panel>
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <Panel title="Latest receipts" className="lg:col-span-2" bodyClassName=""
          actions={<Link to="/receipts" className="inline-flex items-center gap-1 text-[13px] text-accent hover:underline">All receipts <ArrowRight className="size-3.5" /></Link>}>
          {recent.data?.items.length ? <ReceiptTable receipts={recent.data.items} compact /> : recent.isLoading ? <Skeleton className="m-4 h-24" /> : (
            <EmptyState title="No receipts yet" action={<LinkButton to="/upload" variant="primary">Upload your first receipt</LinkButton>}>
              Upload a photo or PDF of a receipt. Gemini extracts it, rules check it, and you confirm anything doubtful.
            </EmptyState>
          )}
        </Panel>
        <Panel title="Extraction health · 30 days"
          actions={<Link to="/model-calls" className="text-[13px] text-accent hover:underline">Details</Link>}>
          {calls.data ? (
            calls.data.stats.calls === 0 ? <p className="text-ink-2">No model calls recorded yet.</p> : (
              <dl className="grid grid-cols-2 gap-4 text-[13px]">
                <div><dt className="text-ink-2">Model calls</dt><dd className="num text-lg font-semibold">{calls.data.stats.calls}</dd></div>
                <div><dt className="text-ink-2">Success rate</dt><dd className="num text-lg font-semibold">
                  {calls.data.stats.success_rate === null ? "—" : `${(calls.data.stats.success_rate * 100).toFixed(1)}%`}</dd></div>
                <div><dt className="text-ink-2">Latency p50</dt><dd className="num text-lg font-semibold">{formatMs(calls.data.stats.latency_p50_ms)}</dd></div>
                <div><dt className="text-ink-2">Latency p95</dt><dd className="num text-lg font-semibold">{formatMs(calls.data.stats.latency_p95_ms)}</dd></div>
              </dl>
            )
          ) : <Skeleton className="h-20" />}
        </Panel>
      </div>
    </>
  );
}
