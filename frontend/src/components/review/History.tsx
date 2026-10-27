import { formatDateTime, formatMs, titleCase } from "../../lib/format";
import type { ReceiptDetail } from "../../lib/types";
import { cx } from "../ui";

function show(value: unknown): string {
  if (value === null || value === undefined || value === "") return "empty";
  if (Array.isArray(value)) return `${value.length} line item${value.length === 1 ? "" : "s"}`;
  return String(value);
}

export default function History({ detail }: { detail: ReceiptDetail }) {
  const events = [
    ...detail.corrections.map((c) => ({
      at: c.created_at,
      title: c.action === "confirmed" ? `Confirmed ${titleCase(c.field)}` : `Corrected ${titleCase(c.field)}`,
      body: c.action === "corrected" ? `${show(c.old_value)} → ${show(c.new_value)}` : null,
      note: c.note,
    })),
    ...detail.decisions.map((d) => ({
      at: d.created_at,
      title: titleCase(d.decision),
      body: d.overridden_checks?.length ? `Approved despite: ${d.overridden_checks.map((c) => c.code).join(", ")}` : null,
      note: d.note,
    })),
  ].sort((a, b) => a.at.localeCompare(b.at));

  return (
    <div className="space-y-6">
      <section>
        <h3 className="mb-2 text-xs font-semibold tracking-wide text-ink-2 uppercase">Review log</h3>
        {events.length === 0 ? <p className="text-[13px] text-ink-2">No corrections or decisions yet.</p> : (
          <ol className="space-y-2 border-l border-line pl-4">
            {events.map((e, i) => (
              <li key={i} className="text-[13px]">
                <p><span className="font-medium">{e.title}</span> <span className="text-ink-3">· {formatDateTime(e.at)}</span></p>
                {e.body && <p className="text-ink-2">{e.body}</p>}
                {e.note && <p className="text-ink-2 italic">“{e.note}”</p>}
              </li>
            ))}
          </ol>
        )}
      </section>

      <section>
        <h3 className="mb-2 text-xs font-semibold tracking-wide text-ink-2 uppercase">Model calls</h3>
        {detail.model_calls.length === 0 ? <p className="text-[13px] text-ink-2">No model calls recorded.</p> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px] text-left text-[13px]">
              <thead className="text-xs text-ink-2">
                <tr><th className="py-1 pr-3 font-medium">When</th><th className="pr-3 font-medium">Attempt</th><th className="pr-3 font-medium">Result</th>
                  <th className="pr-3 text-right font-medium">Tokens in / out</th><th className="text-right font-medium">Latency</th></tr>
              </thead>
              <tbody className="divide-y divide-line">
                {detail.model_calls.map((c, i) => (
                  <tr key={i}>
                    <td className="py-1.5 pr-3 whitespace-nowrap text-ink-2">{formatDateTime(c.created_at)}</td>
                    <td className="num pr-3">{c.attempt}</td>
                    <td className={cx("pr-3", c.status === "error" && "text-bad")}>{c.status === "success" ? "Success" : c.error_category}</td>
                    <td className="num pr-3 text-right">{c.input_tokens ?? "—"} / {c.output_tokens ?? "—"}</td>
                    <td className="num text-right">{formatMs(c.latency_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-1 font-mono text-[11px] text-ink-3">trace {detail.model_calls[0]?.trace_id}</p>
          </div>
        )}
      </section>

      <section>
        <h3 className="mb-2 text-xs font-semibold tracking-wide text-ink-2 uppercase">Extraction versions</h3>
        {detail.extraction_history.length === 0 ? <p className="text-[13px] text-ink-2">None yet.</p> : (
          <ul className="space-y-1 text-[13px]">
            {detail.extraction_history.map((e) => (
              <li key={e.version}>
                v{e.version} · {e.model} · {e.prompt_version} · {e.schema_valid ? "schema-valid" : <span className="text-bad">schema-invalid (not used)</span>}
                {e.current && <span className="ml-1 text-ok">· current</span>}
                <span className="text-ink-3"> · {formatDateTime(e.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
