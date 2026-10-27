import { useQuery } from "@tanstack/react-query";
import { ExternalLink, Maximize2, Minimize2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Button, ErrorBox, Skeleton } from "./ui";

/** Fetches the original with the API's auth header and shows it via an object URL. */
export default function DocumentPreview({ receiptId, contentType, filename }: { receiptId: string; contentType: string; filename: string }) {
  const blob = useQuery({ queryKey: ["receipt-file", receiptId], queryFn: () => api.receiptFile(receiptId), staleTime: Infinity });
  const [url, setUrl] = useState<string | null>(null);
  const [zoomed, setZoomed] = useState(false);

  useEffect(() => {
    if (!blob.data) return;
    const objectUrl = URL.createObjectURL(blob.data);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [blob.data]);

  const isPdf = contentType === "application/pdf";
  return (
    <div className="overflow-hidden rounded-md border border-line bg-surface">
      <div className="flex items-center justify-between gap-2 border-b border-line px-3 py-2">
        <p className="truncate text-[13px] font-medium" title={filename}>{filename}</p>
        <div className="flex shrink-0 gap-1">
          {!isPdf && url && (
            <Button size="sm" variant="ghost" onClick={() => setZoomed((z) => !z)} aria-pressed={zoomed}
              icon={zoomed ? <Minimize2 className="size-3.5" /> : <Maximize2 className="size-3.5" />}>
              {zoomed ? "Fit" : "Zoom"}
            </Button>
          )}
          {url && <a href={url} target="_blank" rel="noreferrer" className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-[13px] text-ink-2 hover:bg-sunken hover:text-ink">
            <ExternalLink className="size-3.5" /> Open</a>}
        </div>
      </div>
      <div className="bg-sunken">
        {blob.error ? <div className="p-4"><ErrorBox error={blob.error} /></div>
          : !url ? <Skeleton className="h-[480px] rounded-none" />
          : isPdf ? <iframe src={url} title={`Original document ${filename}`} className="h-[70vh] w-full" />
          : (
            <div className={zoomed ? "max-h-[75vh] overflow-auto" : "flex max-h-[75vh] justify-center overflow-hidden"}>
              <img src={url} alt={`Original receipt ${filename}`} className={zoomed ? "max-w-none" : "max-h-[75vh] w-auto object-contain"} />
            </div>
          )}
      </div>
    </div>
  );
}
