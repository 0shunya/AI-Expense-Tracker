import { Check, Pencil } from "lucide-react";
import { useState } from "react";
import type { FormEvent } from "react";
import { formatDate, formatDecimal } from "../../lib/format";
import type { ReceiptField } from "../../lib/types";
import { Button, FieldBadge, TextInput, cx } from "../ui";

interface Props {
  fields: ReceiptField[];
  currency: string | null;
  editable: boolean;
  busyField: string | null;
  onSave: (name: string, value: string | null) => Promise<boolean>;
  onConfirm: (name: string) => void;
}

function display(field: ReceiptField, currency: string | null): string {
  if (field.value === null) return "—";
  if (field.money && field.status !== "invalid") return formatDecimal(field.value, currency);
  if (field.name === "date" && field.status !== "invalid") return formatDate(field.value);
  return field.value;
}

const ROW_TONE: Record<string, string> = {
  uncertain: "bg-warn-soft/60",
  invalid: "bg-bad-soft/60",
};

function FieldRow({ field, currency, editable, busy, onSave, onConfirm }:
  { field: ReceiptField; currency: string | null; editable: boolean; busy: boolean; onSave: Props["onSave"]; onConfirm: Props["onConfirm"] }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(field.value ?? "");
  const inputId = `field-${field.name}`;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const ok = await onSave(field.name, draft.trim() === "" ? null : draft.trim());
    if (ok) setEditing(false);
  }

  const corrected = field.status === "verified" && field.extracted_value !== field.value;
  return (
    <tr className={cx("align-top", ROW_TONE[field.status])}>
      <th scope="row" className="w-[132px] px-4 py-2.5 text-left text-[13px] font-medium text-ink-2">
        <label htmlFor={editing ? inputId : undefined}>{field.label}</label>
        {field.required && <span className="text-ink-3" title="Required for approval"> *</span>}
      </th>
      <td className="px-3 py-2">
        {editing ? (
          <form onSubmit={submit} onKeyDown={(e) => { if (e.key === "Escape") { setEditing(false); setDraft(field.value ?? ""); } }}
            className="flex flex-wrap items-center gap-2">
            <TextInput id={inputId} autoFocus value={draft} onChange={(e) => setDraft(e.target.value)}
              type={field.name === "date" ? "date" : "text"} inputMode={field.money ? "decimal" : undefined}
              placeholder={field.money ? "e.g. 12.50" : field.name === "currency" ? "e.g. USD" : field.name === "time" ? "HH:MM" : ""}
              className={cx("h-8 max-w-[240px]", field.money && "num text-right")} />
            <Button size="sm" variant="primary" type="submit" loading={busy}>Save</Button>
            <Button size="sm" variant="ghost" type="button" onClick={() => { setEditing(false); setDraft(field.value ?? ""); }}>Cancel</Button>
          </form>
        ) : (
          <div>
            <p className={cx("font-medium break-words", field.money && "num", field.value === null && "font-normal text-ink-3")}>
              {display(field, currency)}
              {corrected && <span className="ml-2 text-xs font-normal text-ink-3 line-through" title="Value read by the model">
                {field.extracted_value ?? "empty"}</span>}
            </p>
            {field.source_text && field.source_text !== field.value && (
              <p className="mt-0.5 font-mono text-[11px] text-ink-3" title="Exactly as printed on the document">printed: {field.source_text}</p>
            )}
          </div>
        )}
      </td>
      <td className="w-[38%] px-3 py-2.5">
        <FieldBadge status={field.status} />
        {field.reason && field.status !== "missing" && <p className="mt-0.5 text-xs text-ink-2">{field.reason}</p>}
        {field.status === "missing" && field.required && <p className="mt-0.5 text-xs text-ink-2">Required: enter it from the document.</p>}
      </td>
      <td className="w-[76px] px-3 py-2 text-right whitespace-nowrap">
        {editable && !editing && (
          <div className="flex justify-end gap-1">
            {field.status !== "verified" && field.status !== "invalid" && field.value !== null && (
              <button onClick={() => onConfirm(field.name)} disabled={busy}
                className="rounded p-1.5 text-ink-3 hover:bg-ok-soft hover:text-ok disabled:opacity-40"
                aria-label={`Confirm ${field.label}`} title="Confirm value matches the document">
                <Check className="size-4" />
              </button>
            )}
            <button onClick={() => { setDraft(field.value ?? ""); setEditing(true); }}
              className="rounded p-1.5 text-ink-3 hover:bg-sunken hover:text-ink" aria-label={`Edit ${field.label}`} title="Correct value">
              <Pencil className="size-4" />
            </button>
          </div>
        )}
      </td>
    </tr>
  );
}

export default function FieldsTable({ fields, currency, editable, busyField, onSave, onConfirm }: Props) {
  const [showEmpty, setShowEmpty] = useState(false);
  const important = new Set(["merchant", "date", "currency", "total"]);
  const visible = fields.filter((f) => showEmpty || f.value !== null || f.status !== "missing" || important.has(f.name) || f.required);
  const hidden = fields.length - visible.length;
  return (
    <>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[560px]">
          <tbody className="divide-y divide-line">
            {visible.map((f) => (
              <FieldRow key={`${f.name}:${f.value}:${f.status}`} field={f} currency={currency} editable={editable}
                busy={busyField === f.name} onSave={onSave} onConfirm={onConfirm} />
            ))}
          </tbody>
        </table>
      </div>
      {hidden > 0 && (
        <button onClick={() => setShowEmpty(true)} className="w-full border-t border-line px-4 py-2 text-left text-[13px] text-accent hover:bg-canvas">
          Show {hidden} field{hidden === 1 ? "" : "s"} not on this receipt (tip, GSTIN…)
        </button>
      )}
    </>
  );
}
