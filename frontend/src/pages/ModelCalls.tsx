import { useQuery } from "@tanstack/react-query";
import { Activity } from "lucide-react";
import { Link } from "react-router";
import { EmptyState, ErrorBox, PageHeader, Panel, Spinner, Stat, cx } from "../components/ui";
import { api } from "../lib/api";
import { formatDateTime, formatMs } from "../lib/format";

export default function ModelCalls() {
  const query = useQuery({ queryKey: ["model-calls", 100], queryFn: () => api.modelCalls(100), refetchInterval: 15_000 });
  if (query.error) return <ErrorBox error={query.error} onRetry={() => query.refetch()} />;
  if (!query.data) return <Spinner />;
  const { stats, items } = query.data;
  const errors = Object.entries(stats.errors_by_category).sort((a, b) => b[1] - a[1]);

  return (
    <>
      <PageHeader title="Model calls"
        description="Every Gemini attempt goes through one gateway and is logged here: successes, failures and retries. Token counts come from the API's usage metadata; no cost is estimated." />
      <div className="grid grid-cols-2 divide-line rounded-md border border-line bg-surface md:grid-cols-5 md:divide-x">
        <Stat label={`Calls · ${query.data.window_days} days`} value={stats.calls} hint={`${stats.failures} failed`} />
        <Stat label="Success rate" value={stats.success_rate === null ? "—" : `${(stats.success_rate * 100).toFixed(1)}%`} />
        <Stat label="Latency p50" value={formatMs(stats.latency_p50_ms)} hint="successful calls" />
        <Stat label="Latency p95" value={formatMs(stats.latency_p95_ms)} />
        <Stat label="Avg tokens in / out" value={stats.avg_input_tokens === null ? "—" : `${stats.avg_input_tokens} / ${stats.avg_output_tokens}`}
          hint={`${stats.calls_with_token_usage} calls reported usage`} />
      </div>

      {errors.length > 0 && (
        <Panel title="Failures by category" className="mt-6">
          <ul className="flex flex-wrap gap-2">
            {errors.map(([category, count]) => (
              <li key={category} className="rounded border border-line bg-canvas px-2 py-1 font-mono text-xs">{category} <span className="font-semibold">{count}</span></li>
            ))}
          </ul>
        </Panel>
      )}

      <Panel title="Recent attempts" className="mt-6" bodyClassName="">
        {items.length === 0 ? <EmptyState icon={<Activity className="size-6" />} title="No model calls yet">Calls appear once a receipt is extracted.</EmptyState> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[860px] text-left text-[13px]">
              <thead className="border-b border-line bg-canvas text-xs text-ink-2">
                <tr>
                  <th className="px-4 py-2 font-medium">Time</th><th className="px-3 py-2 font-medium">Receipt</th><th className="px-3 py-2 font-medium">Model · prompt</th>
                  <th className="px-3 py-2 font-medium">Attempt</th><th className="px-3 py-2 font-medium">Result</th>
                  <th className="px-3 py-2 text-right font-medium">In</th><th className="px-3 py-2 text-right font-medium">Out</th>
                  <th className="px-3 py-2 text-right font-medium">Thinking</th><th className="px-4 py-2 text-right font-medium">Latency</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {items.map((c, i) => (
                  <tr key={i}>
                    <td className="px-4 py-2 whitespace-nowrap text-ink-2">{formatDateTime(c.created_at)}</td>
                    <td className="px-3 py-2">{c.receipt_id ? <Link to={`/receipts/${c.receipt_id}`} className="font-mono text-xs text-accent hover:underline">{c.receipt_id.slice(0, 8)}</Link> : "—"}</td>
                    <td className="px-3 py-2 font-mono text-xs">{c.model} · {c.prompt_version}</td>
                    <td className="num px-3 py-2">{c.attempt}</td>
                    <td className={cx("px-3 py-2", c.status === "error" && "text-bad")} title={c.error_message ?? undefined}>
                      {c.status === "success" ? "Success" : c.error_category}
                    </td>
                    <td className="num px-3 py-2 text-right">{c.input_tokens ?? "—"}</td>
                    <td className="num px-3 py-2 text-right">{c.output_tokens ?? "—"}</td>
                    <td className="num px-3 py-2 text-right">{c.thinking_tokens ?? "—"}</td>
                    <td className="num px-4 py-2 text-right">{formatMs(c.latency_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </>
  );
}
