import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { formatDecimal } from "../../lib/format";
import type { LineItem, LineItemInput } from "../../lib/types";
import { Button, FieldBadge, cx } from "../ui";

const COLUMNS: { key: keyof LineItemInput; label: string; numeric?: boolean; width: string }[] = [
  { key: "description", label: "Description", width: "min-w-[180px]" },
  { key: "quantity", label: "Qty", numeric: true, width: "w-[72px]" },
  { key: "unit_price", label: "Unit price", numeric: true, width: "w-[100px]" },
  { key: "discount", label: "Discount", numeric: true, width: "w-[90px]" },
  { key: "line_total", label: "Line total", numeric: true, width: "w-[104px]" },
];

function toInput(item: LineItem): LineItemInput {
  return { description: item.description, quantity: item.quantity, unit_price: item.unit_price,
    line_total: item.line_total, discount: item.discount, tax: item.tax };
}

export default function LineItems({ items, currency, editable, saving, onSave }:
  { items: LineItem[]; currency: string | null; editable: boolean; saving: boolean; onSave: (rows: LineItemInput[]) => Promise<boolean> }) {
  const [draft, setDraft] = useState<LineItemInput[] | null>(null);

  if (draft) {
    const set = (index: number, key: keyof LineItemInput, value: string) =>
      setDraft((rows) => rows!.map((r, i) => (i === index ? { ...r, [key]: value === "" ? null : value } : r)));
    return (
      <div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left">
            <thead className="border-b border-line bg-canvas text-xs text-ink-2">
              <tr>{COLUMNS.map((c) => <th key={c.key} scope="col" className={cx("px-2 py-2 font-medium", c.numeric && "text-right")}>{c.label}</th>)}<th className="w-10" /></tr>
            </thead>
            <tbody>
              {draft.map((row, index) => (
                <tr key={index} className="border-b border-line">
                  {COLUMNS.map((c) => (
                    <td key={c.key} className={cx("px-1.5 py-1.5", c.width)}>
                      <input value={row[c.key] ?? ""} onChange={(e) => set(index, c.key, e.target.value)}
                        aria-label={`Line ${index + 1} ${c.label}`} inputMode={c.numeric ? "decimal" : undefined}
                        className={cx("h-8 w-full rounded border border-line-strong px-2 text-[13px] focus:border-accent focus:outline-none",
                          c.numeric && "num text-right")} />
                    </td>
                  ))}
                  <td className="px-1 text-center">
                    <button onClick={() => setDraft((rows) => rows!.filter((_, i) => i !== index))}
                      className="rounded p-1.5 text-ink-3 hover:bg-bad-soft hover:text-bad" aria-label={`Remove line ${index + 1}`}>
                      <Trash2 className="size-4" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3">
          <Button size="sm" icon={<Plus className="size-3.5" />}
            onClick={() => setDraft((rows) => [...rows!, { description: null, quantity: "1", unit_price: null, line_total: null, discount: null, tax: null }])}>
            Add line
          </Button>
          <div className="flex gap-2">
            <Button size="sm" variant="ghost" onClick={() => setDraft(null)}>Cancel</Button>
            <Button size="sm" variant="primary" loading={saving} onClick={async () => { if (await onSave(draft)) setDraft(null); }}>Save line items</Button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div>
      {items.length === 0 ? (
        <p className="px-4 py-4 text-ink-2">No line items were read from this document.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-left">
            <thead className="border-b border-line bg-canvas text-xs text-ink-2">
              <tr>
                {COLUMNS.map((c) => <th key={c.key} scope="col" className={cx("px-3 py-2 font-medium", c.numeric && "text-right", c.key === "description" && "pl-4")}>{c.label}</th>)}
                <th scope="col" className="px-4 py-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {items.map((item) => (
                <tr key={item.position} className={cx("align-top", item.status === "uncertain" && "bg-warn-soft/60", item.status === "invalid" && "bg-bad-soft/60")}>
                  <td className="py-2 pr-3 pl-4">
                    <p className="font-medium">{item.description ?? <span className="text-ink-3">—</span>}</p>
                    {item.source_text && <p className="font-mono text-[11px] text-ink-3">{item.source_text}</p>}
                  </td>
                  <td className="num px-3 py-2 text-right">{item.quantity ?? "—"}</td>
                  <td className="num px-3 py-2 text-right">{formatDecimal(item.unit_price, currency)}</td>
                  <td className="num px-3 py-2 text-right">{item.discount ? formatDecimal(item.discount, currency) : "—"}</td>
                  <td className="num px-3 py-2 text-right font-medium">{formatDecimal(item.line_total, currency)}</td>
                  <td className="px-4 py-2">
                    <FieldBadge status={item.status} />
                    {item.reason && item.status !== "verified" && <p className="mt-0.5 max-w-[220px] text-xs text-ink-2">{item.reason}</p>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {editable && (
        <div className="border-t border-line px-4 py-2.5">
          <Button size="sm" onClick={() => setDraft(items.map(toInput))}>Edit line items</Button>
        </div>
      )}
    </div>
  );
}
