import { Copy, FileText, Image as ImageIcon, TriangleAlert } from "lucide-react";
import { Link, useNavigate } from "react-router";
import { formatDate, formatMinor, relativeTime } from "../lib/format";
import type { ReceiptSummary } from "../lib/types";
import { StatusBadge, cx } from "./ui";

export function FlagSummary({ receipt }: { receipt: ReceiptSummary }) {
  const flags = [];
  if (receipt.duplicate_status === "probable") {
    flags.push(<span key="d" className="inline-flex items-center gap-1 text-warn"><Copy className="size-3.5" aria-hidden />Possible duplicate</span>);
  }
  if (receipt.status === "needs_review" && receipt.review_reason_count > 0) {
    flags.push(<span key="r" className="inline-flex items-center gap-1 text-warn"><TriangleAlert className="size-3.5" aria-hidden />
      {receipt.review_reason_count} issue{receipt.review_reason_count === 1 ? "" : "s"}</span>);
  }
  if (receipt.status === "needs_review" && receipt.review_reason_count === 0) {
    flags.push(<span key="c" className="text-ink-3">No issues found</span>);
  }
  if (receipt.status === "failed" && receipt.error_message) {
    flags.push(<span key="f" className="text-bad">{receipt.error_message}</span>);
  }
  return <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs">{flags}</div>;
}

export default function ReceiptTable({ receipts, compact = false }: { receipts: ReceiptSummary[]; compact?: boolean }) {
  const navigate = useNavigate();
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-left">
        <thead className="border-b border-line bg-canvas text-xs font-medium text-ink-2">
          <tr>
            <th scope="col" className="px-4 py-2 font-medium">Merchant</th>
            <th scope="col" className="px-3 py-2 font-medium">Receipt date</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">Total</th>
            <th scope="col" className="px-3 py-2 font-medium">Status</th>
            {!compact && <th scope="col" className="px-3 py-2 font-medium">Flags</th>}
            <th scope="col" className="px-4 py-2 text-right font-medium">Uploaded</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {receipts.map((r) => (
            <tr key={r.id} onClick={() => navigate(`/receipts/${r.id}`)} className="cursor-pointer hover:bg-canvas">
              <td className="max-w-[280px] px-4 py-2.5">
                <Link to={`/receipts/${r.id}`} onClick={(e) => e.stopPropagation()} className="flex items-center gap-2 font-medium hover:text-accent">
                  {r.content_type === "application/pdf"
                    ? <FileText className="size-4 shrink-0 text-ink-3" aria-hidden />
                    : <ImageIcon className="size-4 shrink-0 text-ink-3" aria-hidden />}
                  <span className={cx("truncate", !r.merchant && "text-ink-2 italic")}>{r.merchant ?? r.original_filename}</span>
                </Link>
              </td>
              <td className="num px-3 py-2.5 whitespace-nowrap text-ink-2">{formatDate(r.receipt_date)}</td>
              <td className="num px-3 py-2.5 text-right font-medium whitespace-nowrap">{formatMinor(r.total_minor, r.currency)}</td>
              <td className="px-3 py-2.5"><StatusBadge status={r.status} approvedBy={r.approved_by} /></td>
              {!compact && <td className="max-w-[320px] px-3 py-2.5"><FlagSummary receipt={r} /></td>}
              <td className="px-4 py-2.5 text-right text-xs whitespace-nowrap text-ink-3">{relativeTime(r.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
