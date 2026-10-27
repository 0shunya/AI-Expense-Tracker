/** Client-side pre-checks for fast feedback. The server re-validates by content and is authoritative. */
export const ACCEPTED_TYPES: Record<string, string> = {
  "image/jpeg": "JPEG",
  "image/png": "PNG",
  "image/webp": "WebP",
  "application/pdf": "PDF",
};
const EXTENSIONS = [".jpg", ".jpeg", ".png", ".webp", ".pdf"];

export function precheckFile(file: File, maxMb: number): string | null {
  const name = file.name.toLowerCase();
  const typeOk = file.type in ACCEPTED_TYPES || (file.type === "" && EXTENSIONS.some((ext) => name.endsWith(ext)));
  if (!typeOk) return `${file.name}: only JPEG, PNG, WebP or PDF files are supported.`;
  if (file.size === 0) return `${file.name}: the file is empty.`;
  if (file.size > maxMb * 1024 * 1024) return `${file.name}: larger than the ${maxMb} MB limit.`;
  return null;
}
