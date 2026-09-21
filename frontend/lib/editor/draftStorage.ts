import type { Override } from "@/lib/api/types";

export interface EditorDraft {
  drafts: Record<string, Override[]>;
  order: string[] | null;
  logo: boolean | null;
  pending: { jobId: string; position: number } | null;
}

export const DRAFT_PREFIX = "pd.editor-draft.v1:";
const memory = new Map<string, EditorDraft>();

/** No blob URLs: uploaded file IDs remain part of the patch, previews are ephemeral. */
export function readEditorDraft(key: string): EditorDraft | null {
  if (memory.has(key)) return memory.get(key)!;
  try {
    const value = JSON.parse(localStorage.getItem(DRAFT_PREFIX + key) ?? "null");
    if (!value || typeof value.drafts !== "object" || value.drafts === null || Array.isArray(value.drafts)) return null;
    const ops = new Set(["add_text", "text", "style", "geometry", "picture", "delete", "background"]);
    if (!Object.values(value.drafts).every((list) => Array.isArray(list) && list.every((op) => op && ops.has(op.op)))) return null;
    if (value.order !== null && (!Array.isArray(value.order) || !value.order.every((id: unknown) => typeof id === "string"))) return null;
    if (value.logo !== null && typeof value.logo !== "boolean") return null;
    if (value.pending !== null && (!value.pending || typeof value.pending.jobId !== "string" || !Number.isInteger(value.pending.position))) return null;
    return value as EditorDraft;
  } catch {
    return null;
  }
}

/** Keep a memory fallback, but never claim durable storage when the browser refused it. */
export function writeEditorDraft(key: string, value: EditorDraft | null): boolean {
  if (value) memory.set(key, value);
  else memory.delete(key);
  try {
    if (value) localStorage.setItem(DRAFT_PREFIX + key, JSON.stringify(value));
    else localStorage.removeItem(DRAFT_PREFIX + key);
    return true;
  } catch {
    return false;
  }
}

/** Старые черновики доступны для просмотра, но не для наложения на другую ревизию. */
export function draftRevisions(jobId: string, variantId: string): number[] {
  const prefix = `${jobId}:${variantId}:`;
  const keys = new Set(memory.keys());
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key?.startsWith(DRAFT_PREFIX)) keys.add(key.slice(DRAFT_PREFIX.length));
    }
  } catch { /* память вкладки остаётся доступной */ }
  return [...keys].filter((key) => key.startsWith(prefix) && readEditorDraft(key))
    .map((key) => Number(key.slice(prefix.length))).filter((n) => Number.isInteger(n) && n > 0).sort((a, b) => b - a);
}