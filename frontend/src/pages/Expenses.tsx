import { useQuery } from "@tanstack/react-query";
import { Download, WalletCards } from "lucide-react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { useToast } from "../components/toast";
import { Button, EmptyState, ErrorBox, Label, PageHeader, Panel, Select, Spinner } from "../components/ui";
import { api } from "../lib/api";
import { formatDate, formatMinor, formatMonth, titleCase } from "../lib/format";

export default function Expenses() {
  const [params, setParams] = useSearchParams();
  const month = params.get("month") ?? "";
  const category = params.get("category") ?? "";
  const toast = useToast();
  const [exporting, setExporting] = useState(false);
  const all = useQuery({ queryKey: ["expenses", "", ""], queryFn: () => api.expenses() });
  const list = useQuery({ queryKey: ["expenses", month, category], queryFn: () => api.expenses(month || undefined, category || undefined) });

  function set(key: string, value: string) {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value) next.set(key, value); else next.delete(key);
      return next;
    });
  }

  async function exportCsv() {
    setExporting(true);
    try {
      const blob = await api.exportExpenses(month || undefined, category || undefined);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `expenses-${month || "all"}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Export failed.");
    } finally {
      setExporting(false);
    }
  }

  const months = all.data?.available_months ?? [];
  const categories = [...new Set((all.data?.items ?? []).map((e) => e.category))].sort();
  const data = list.data;

  return (
    <>
      <PageHeader title="Expense tracker"
        description="Approved receipts only. Each row links back to the receipt and its review history."
        actions={<Button icon={<Download className="size-4" />} loading={exporting} disabled={!data?.items.length} onClick={exportCsv}>Export CSV</Button>} />

      <div className="mb-5 flex flex-wrap gap-3">
        <div className="w-44">
          <Label htmlFor="e-month">Month</Label>
          <Select id="e-month" value={month} onChange={(e) => set("month", e.target.value)}>
            <option value="">All months</option>
            {months.map((m) => <option key={m} value={m}>{formatMonth(m)}</option>)}
          </Select>
        </div>
        <div className="w-44">
          <Label htmlFor="e-cat">Category</Label>
          <Select id="e-cat" value={category} onChange={(e) => set("category", e.target.value)}>
            <option value="">All categories</option>
            {categories.map((c) => <option key={c} value={c}>{titleCase(c)}</option>)}
          </Select>
        </div>
      </div>

      {list.error ? <ErrorBox error={list.error} onRetry={() => list.refetch()} /> : !data ? <Spinner /> : data.items.length === 0 ? (
        <Panel><EmptyState icon={<WalletCards className="size-6" />} title={month || category ? "No approved expenses for this filter" : "No approved expenses yet"}>
          Expenses are added when you approve a reviewed receipt.</EmptyState></Panel>
      ) : (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
          <Panel bodyClassName="">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[560px] text-left">
                <thead className="border-b border-line bg-canvas text-xs text-ink-2">
                  <tr><th className="px-4 py-2 font-medium">Date</th><th className="px-3 py-2 font-medium">Merchant</th>
                    <th className="px-3 py-2 font-medium">Category</th><th className="px-3 py-2 font-medium">Approved by</th>
                    <th className="px-4 py-2 text-right font-medium">Amount</th></tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {data.items.map((e) => (
                    <tr key={e.id}>
                      <td className="num px-4 py-2.5 whitespace-nowrap text-ink-2">{formatDate(e.expense_date)}</td>
                      <td className="px-3 py-2.5"><Link to={`/receipts/${e.receipt_id}`} className="font-medium hover:text-accent">{e.merchant}</Link></td>
                      <td className="px-3 py-2.5 text-ink-2">{titleCase(e.category)}</td>
                      <td className="px-3 py-2.5 text-ink-2">{e.approved_by === "auto_rules" ? "Rules (auto)" : "Reviewer"}</td>
                      <td className="num px-4 py-2.5 text-right font-medium whitespace-nowrap">
                        {e.kind === "refund" ? <span className="text-ok">−{formatMinor(e.total_minor, e.currency)} refund</span> : formatMinor(e.total_minor, e.currency)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
          <div className="space-y-4">
            <Panel title="Total">
              <dl className="space-y-2">
                {data.totals_by_currency.map((t) => (
                  <div key={t.currency} className="flex items-baseline justify-between">
                    <dt className="text-ink-2">{t.currency}</dt><dd className="num text-lg font-semibold">{formatMinor(t.total_minor, t.currency)}</dd>
                  </div>
                ))}
              </dl>
              {data.totals_by_currency.length > 1 && <p className="mt-2 text-xs text-ink-3">Currencies are not converted or summed together.</p>}
            </Panel>
            <Panel title="By category">
              <dl className="space-y-1.5 text-[13px]">
                {data.totals_by_category.map((t) => (
                  <div key={`${t.currency}-${t.category}`} className="flex justify-between gap-2">
                    <dt className="text-ink-2">{titleCase(t.category)}</dt><dd className="num font-medium">{formatMinor(t.total_minor, t.currency)}</dd>
                  </div>
                ))}
              </dl>
            </Panel>
          </div>
        </div>
      )}
    </>
  );
}
