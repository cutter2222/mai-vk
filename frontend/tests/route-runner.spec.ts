import { expect, test } from "@playwright/test";

import { runSteps, undo, type RunContext } from "../components/project/chat/routeRunner";
import { api } from "../lib/api/client";
import { getProject, loadProject } from "../lib/state/projects";

test("separate rebuild steps wait and use the completed revision", async () => {
  const getJob = api.jobs.get;
  const getGeneration = api.generations.get;
  const revisions: number[] = [];
  let revision = 1;
  const variant = () => ({ variant_id: "balanced", revision, status: "ready", slide_count: 3, artifacts: { pptx: "deck.pptx" } });
  api.jobs.get = async () => ({ status: "succeeded" }) as Awaited<ReturnType<typeof getJob>>;
  api.generations.get = async () => ({ variants: [variant()] }) as Awaited<ReturnType<typeof getGeneration>>;
  const ctx = {
    projectId: "runner-test", liveCount: null, office: null, say: () => {},
    session: { jobId: "job_test", variant: variant(), job: { refresh: () => {} } },
    rebuild: async (_instruction: string, target: { revision: number }) => {
      revisions.push(target.revision);
      revision++;
      return `edit_${revision}`;
    },
  } as unknown as RunContext;
  try {
    await runSteps(ctx, [
      { action: "slide_rebuild", slides: [1], instruction: "first" },
      { action: "slide_rebuild", slides: [2], instruction: "second" },
    ]);
    expect(revisions).toEqual([1, 2]);
    expect(ctx.session.variant?.revision).toBe(3);
  } finally {
    api.jobs.get = getJob;
    api.generations.get = getGeneration;
  }
});

test("a failed asynchronous rebuild stops following steps", async () => {
  const getJob = api.jobs.get;
  api.jobs.get = async () => ({ status: "failed" }) as Awaited<ReturnType<typeof getJob>>;
  let calls = 0;
  const messages: string[] = [];
  const ctx = {
    projectId: "runner-test", liveCount: null, office: null, say: (text: string) => messages.push(text),
    session: { jobId: "job_test", variant: { variant_id: "balanced", revision: 1, status: "ready", slide_count: 3, artifacts: { pptx: "deck.pptx" } } },
    rebuild: async () => { calls++; return "failed_edit"; },
  } as unknown as RunContext;
  try {
    await runSteps(ctx, [
      { action: "slide_rebuild", slides: [1] },
      { action: "slide_rebuild", slides: [2] },
    ]);
    expect(calls).toBe(1);
    expect(messages.join()).toContain("остальные слайды не трогаю");
  } finally { api.jobs.get = getJob; }
});

for (const transferFails of [false, true]) {
  test(`mixed steps await the open-document acknowledgment (${transferFails})`, async () => {
    const originals = { job: api.jobs.get, generation: api.generations.get };
    const calls: string[] = [];
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const variant = { variant_id: "balanced", revision: 2, status: "ready", slide_count: 3, artifacts: { pptx: "balanced/r2/deck.pptx" } };
    api.jobs.get = async () => ({ status: "succeeded" }) as Awaited<ReturnType<typeof originals.job>>;
    api.generations.get = async () => ({ variants: [variant], edits: [{ edit_job_id: "edit_2", result: "applied", new_revision: 2 }] }) as Awaited<ReturnType<typeof originals.generation>>;
    const ctx = {
      projectId: "mixed-test", liveCount: null, say: (text: string) => calls.push(text),
      session: { jobId: "job_test", variant: { ...variant, revision: 1 }, job: { refresh: () => {} } },
      rebuild: async () => "edit_2",
      office: {
        waitForApplied: async (...args: unknown[]) => {
          expect(args).toEqual(["job_test", "balanced", 2, "edit_2"]);
          calls.push("applying");
          await gate;
          if (transferFails) throw new Error("transfer failed");
          calls.push("applied");
        },
        run: async () => { calls.push("office edit"); return { changed: false, message: "no-op" }; },
      },
    } as unknown as RunContext;
    let running: Promise<void> | undefined;
    try {
      running = runSteps(ctx, [{ action: "slide_rebuild", slides: [1] }, { action: "deck_text", slides: [2], instruction: "next" }]);
      await expect.poll(() => calls).toContain("applying");
      expect(calls).not.toContain("office edit");
      release();
      await running;
      if (transferFails) {
        expect(calls).toContain("transfer failed");
        expect(calls).not.toContain("office edit");
      } else expect(calls.slice(0, 3)).toEqual(["applying", "applied", "office edit"]);
    } finally {
      release();
      await running;
      api.jobs.get = originals.job;
      api.generations.get = originals.generation;
    }
  });
}

for (const succeeded of [false, true]) {
  test(`rebuild undo marks the original only after success (${succeeded})`, async () => {
    const originals = { get: api.projects.get, patch: api.projects.patchEvent, revert: api.generations.revert, job: api.jobs.get, generation: api.generations.get };
    const projectId = `undo-runner-${succeeded}`;
    const event = { event_id: "edit-event", role: "assistant", kind: "edit_card", job_id: "job_test", variant_id: "balanced", edit_job_id: "edit_1", slide_index: 0 };
    const variant = { variant_id: "balanced", revision: 2 };
    const result = { variants: [variant], edits: [{ edit_job_id: "edit_1", result: "applied", new_revision: 2, base_revision: 1, slide_index: 0 }] };
    let reverted = 0;
    api.projects.get = async () => ({ project_id: projectId, settings: {}, brief: {}, events: [event] }) as Awaited<ReturnType<typeof originals.get>>;
    api.projects.patchEvent = async () => ({ ...event, undone: true }) as Awaited<ReturnType<typeof originals.patch>>;
    api.generations.revert = async () => { reverted++; return { edit_job_id: "undo_1" }; };
    api.jobs.get = async () => ({ status: succeeded ? "succeeded" : "failed" }) as Awaited<ReturnType<typeof originals.job>>;
    api.generations.get = async () => result as Awaited<ReturnType<typeof originals.generation>>;
    const ctx = { projectId, say: () => {}, session: { jobId: "job_test", variant, result, job: { refresh: () => {} } } } as unknown as RunContext;
    try {
      await loadProject(projectId);
      expect(await undo(ctx, "edit-event")).toBe(succeeded);
      expect(getProject(projectId)?.events).toHaveLength(1);
      expect(getProject(projectId)?.events[0]).toMatchObject(succeeded ? { undone: true } : event);
      if (!succeeded) expect(getProject(projectId)?.events[0]).not.toHaveProperty("undone", true);
      if (succeeded) {
        expect(await undo(ctx)).toBe(false);
        expect(reverted).toBe(1);
      }
    } finally {
      api.projects.get = originals.get;
      api.projects.patchEvent = originals.patch;
      api.generations.revert = originals.revert;
      api.jobs.get = originals.job;
      api.generations.get = originals.generation;
    }
  });
}