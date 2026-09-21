import { expect, test } from "@playwright/test";

import { DRAFT_PREFIX, readEditorDraft, writeEditorDraft, type EditorDraft } from "../lib/editor/draftStorage";

test("draft storage: job/variant/revision isolation, reopen, invalid data, quota", () => {
  const data = new Map<string, string>();
  const original = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => data.set(key, value),
    removeItem: (key: string) => data.delete(key),
  } });
  try {
    const draft: EditorDraft = { drafts: { s1: [{ op: "text", target: { object_id: "title" }, text: "manual" }] }, order: ["s2", "s1"], logo: true, pending: null };
    expect(writeEditorDraft("jobA:compact:1", draft)).toBe(true);
    expect(readEditorDraft("jobA:compact:1")).toEqual(draft);
    for (const key of ["jobB:compact:1", "jobA:balanced:1", "jobA:compact:2"]) expect(readEditorDraft(key)).toBeNull();
    data.set(DRAFT_PREFIX + "reopened", JSON.stringify(draft));
    expect(readEditorDraft("reopened")).toEqual(draft);
    data.set(DRAFT_PREFIX + "broken", "{");
    expect(readEditorDraft("broken")).toBeNull();
    data.set(DRAFT_PREFIX + "invalid", JSON.stringify({ ...draft, drafts: { s1: [null] } }));
    expect(readEditorDraft("invalid")).toBeNull();
    expect(writeEditorDraft("jobA:compact:1", null)).toBe(true);
    expect(readEditorDraft("jobA:compact:1")).toBeNull();
    Object.defineProperty(globalThis, "localStorage", { configurable: true, get: () => { throw new Error("blocked"); } });
    expect(writeEditorDraft("blocked", draft)).toBe(false);
    expect(readEditorDraft("blocked")).toEqual(draft);
  } finally {
    if (original) Object.defineProperty(globalThis, "localStorage", original);
    else Reflect.deleteProperty(globalThis, "localStorage");
  }
});