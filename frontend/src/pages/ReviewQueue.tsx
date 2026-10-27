import { useQuery } from "@tanstack/react-query";
import { ClipboardCheck } from "lucide-react";
import ReceiptTable from "../components/ReceiptTable";
import { EmptyState, ErrorBox, LinkButton, PageHeader, Panel, Spinner } from "../components/ui";
import { api } from "../lib/api";

export default function ReviewQueue() {
  const queue = useQuery({ queryKey: ["review-queue"], queryFn: api.reviewQueue, refetchInterval: 10_000 });
  const first = queue.data?.items[0];
  return (
    <>
      <PageHeader title="Review queue"
        description="Receipts waiting for a human decision, oldest first. Doubtful fields are highlighted with the rule that raised them."
        actions={first && <LinkButton to={`/receipts/${first.id}`} variant="primary">Start with the oldest</LinkButton>} />
      <Panel bodyClassName="">
        {queue.error ? <div className="p-4"><ErrorBox error={queue.error} onRetry={() => queue.refetch()} /></div>
          : !queue.data ? <Spinner />
          : queue.data.items.length === 0
            ? <EmptyState icon={<ClipboardCheck className="size-6" />} title="Nothing to review">
                New extractions appear here after Gemini reads them and the validators run.</EmptyState>
            : <ReceiptTable receipts={queue.data.items} />}
      </Panel>
    </>
  );
}
