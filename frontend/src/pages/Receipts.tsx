import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ReceiptText, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import ReceiptTable from "../components/ReceiptTable";
import { Button, EmptyState, ErrorBox, Label, LinkButton, PageHeader, Panel, Select, Spinner, TextInput } from "../components/ui";
import { api } from "../lib/api";
import { RECEIPT_STATUS_LABEL, isInFlight } from "../lib/format";
import type { ReceiptStatus } from "../lib/types";

const PAGE_SIZE = 25;
const FILTER_KEYS = ["q", "merchant", "status", "date_from", "date_to", "duplicates"] as const;

export default function Receipts() {
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const [merchant, setMerchant] = useState(params.get("merchant") ?? "");
  const page = Math.max(0, Number(params.get("page") ?? 0) || 0);

  // Debounce free-text filters into the URL so the list is shareable and back/forward works.
  useEffect(() => {
    const id = window.setTimeout(() => {
      setParams((prev) => {
        const next = new URLSearchParams(prev);
        for (const [key, value] of [["q", q], ["merchant", merchant]] as const) {
          if (value.trim()) next.set(key, value.trim()); else next.delete(key);
        }
        if ((prev.get("q") ?? "") !== q.trim() || (prev.get("merchant") ?? "") !== merchant.trim()) next.delete("page");
        return next;
      }, { replace: true });
    }, 300);
    return () => window.clearTimeout(id);
  }, [q, merchant, setParams]);

  function setFilter(key: string, value: string) {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value) next.set(key, value); else next.delete(key);
      next.delete("page");
      return next;
    });
  }

  function setPage(next: number) {
    setParams((prev) => {
      const out = new URLSearchParams(prev);
      if (next > 0) out.set("page", String(next)); else out.delete("page");
      return out;
    });
  }

  const query = {
    q: params.get("q") ?? undefined,
    merchant: params.get("merchant") ?? undefined,
    status: params.get("status") ?? undefined,
    date_from: params.get("date_from") ?? undefined,
    date_to: params.get("date_to") ?? undefined,
    duplicates_only: params.get("duplicates") === "1",
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  };
  const list = useQuery({
    queryKey: ["receipts", query],
    queryFn: () => api.receipts(query),
    placeholderData: keepPreviousData,
    refetchInterval: (q) => (q.state.data?.items.some((r) => isInFlight(r.status)) ? 3000 : false),
  });
  const filtered = FILTER_KEYS.some((k) => params.get(k));
  const total = list.data?.total ?? 0;

  return (
    <>
      <PageHeader title="Receipts" description="Every uploaded document with its extraction status. Click a row to inspect or review it." />
      <Panel bodyClassName="">
        <div className="grid gap-3 border-b border-line p-4 sm:grid-cols-2 lg:grid-cols-[1.4fr_1fr_160px_150px_150px_auto]">
          <div>
            <Label htmlFor="f-q">Search</Label>
            <div className="relative">
              <Search className="pointer-events-none absolute top-2.5 left-2.5 size-4 text-ink-3" aria-hidden />
              <TextInput id="f-q" className="pl-8" placeholder="Merchant, file name or reference" value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
          </div>
          <div>
            <Label htmlFor="f-merchant">Merchant</Label>
            <TextInput id="f-merchant" placeholder="e.g. Blue Oak" value={merchant} onChange={(e) => setMerchant(e.target.value)} />
          </div>
          <div>
            <Label htmlFor="f-status">Status</Label>
            <Select id="f-status" value={params.get("status") ?? ""} onChange={(e) => setFilter("status", e.target.value)}>
              <option value="">Any status</option>
              {(Object.keys(RECEIPT_STATUS_LABEL) as ReceiptStatus[]).map((s) => <option key={s} value={s}>{RECEIPT_STATUS_LABEL[s]}</option>)}
            </Select>
          </div>
          <div>
            <Label htmlFor="f-from">Receipt date from</Label>
            <TextInput id="f-from" type="date" value={params.get("date_from") ?? ""} onChange={(e) => setFilter("date_from", e.target.value)} />
          </div>
          <div>
            <Label htmlFor="f-to">to</Label>
            <TextInput id="f-to" type="date" value={params.get("date_to") ?? ""} onChange={(e) => setFilter("date_to", e.target.value)} />
          </div>
          <div className="flex items-end gap-3">
            <label className="flex h-9 items-center gap-2 text-[13px] whitespace-nowrap">
              <input type="checkbox" className="size-4 accent-accent" checked={params.get("duplicates") === "1"}
                onChange={(e) => setFilter("duplicates", e.target.checked ? "1" : "")} />
              Duplicate flags
            </label>
            {filtered && <Button variant="ghost" size="sm" className="mb-1" onClick={() => { setQ(""); setMerchant(""); setParams({}); }}>Clear</Button>}
          </div>
        </div>

        {list.error ? <div className="p-4"><ErrorBox error={list.error} onRetry={() => list.refetch()} /></div>
          : !list.data ? <Spinner label="Loading receipts" />
          : list.data.items.length === 0 ? (
            filtered
              ? <EmptyState icon={<Search className="size-6" />} title="No receipts match these filters">Try a wider date range or clear the filters.</EmptyState>
              : <EmptyState icon={<ReceiptText className="size-6" />} title="No receipts yet" action={<LinkButton to="/upload" variant="primary">Upload receipts</LinkButton>}>
                  Uploaded receipts will be listed here with their review status.</EmptyState>
          ) : (
            <>
              <ReceiptTable receipts={list.data.items} />
              <div className="flex items-center justify-between border-t border-line px-4 py-2.5 text-[13px] text-ink-2">
                <span className="num">{page * PAGE_SIZE + 1}–{Math.min(total, (page + 1) * PAGE_SIZE)} of {total}</span>
                <div className="flex gap-2">
                  <Button size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</Button>
                  <Button size="sm" disabled={(page + 1) * PAGE_SIZE >= total} onClick={() => setPage(page + 1)}>Next</Button>
                </div>
              </div>
            </>
          )}
      </Panel>
    </>
  );
}
