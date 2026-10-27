import { CheckCircle2, CircleAlert, Info, TriangleAlert } from "lucide-react";
import { useState } from "react";
import type { Check } from "../../lib/types";

const ICONS = {
  error: <CircleAlert className="mt-0.5 size-4 shrink-0 text-bad" aria-label="Failed (error)" />,
  warning: <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warn" aria-label="Failed (warning)" />,
  info: <Info className="mt-0.5 size-4 shrink-0 text-ink-3" aria-label="Note" />,
};

export default function Checks({ checks }: { checks: Check[] }) {
  const [showPassed, setShowPassed] = useState(false);
  const failed = checks.filter((c) => !c.passed);
  const passed = checks.filter((c) => c.passed);
  return (
    <div className="space-y-2">
      {failed.length === 0 && <p className="flex items-center gap-2 text-ok"><CheckCircle2 className="size-4" /> Every deterministic check passed.</p>}
      <ul className="space-y-2">
        {failed.map((c, i) => (
          <li key={`${c.code}-${i}`} className="flex gap-2.5">
            {ICONS[c.severity]}
            <div>
              <p className="text-[13px]">{c.message}</p>
              <p className="font-mono text-[11px] text-ink-3">{c.code}{c.fields.length ? ` · ${c.fields.join(", ")}` : ""}</p>
            </div>
          </li>
        ))}
      </ul>
      {passed.length > 0 && (
        <div>
          <button onClick={() => setShowPassed((s) => !s)} className="text-[13px] text-accent hover:underline" aria-expanded={showPassed}>
            {showPassed ? "Hide" : "Show"} {passed.length} passed check{passed.length === 1 ? "" : "s"}
          </button>
          {showPassed && (
            <ul className="mt-2 space-y-1.5">
              {passed.map((c, i) => (
                <li key={`${c.code}-${i}`} className="flex gap-2.5 text-[13px] text-ink-2">
                  <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-ok" aria-hidden />{c.message}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
