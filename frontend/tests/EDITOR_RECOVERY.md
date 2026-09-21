# Editor recovery regression

The existing React/Mantine editor and server-side PPTX composition remain in use.
No office server, new editor framework or paid SDK is required.

## Behaviour

- Draft operations, slide order, logo choice and accepted patch job ID are saved in
  browser localStorage under `pd.editor-draft.v1:<job>:<variant>:<revision>`.
- Reload/reopening restores only the matching revision. Older drafts can be viewed
  and deleted, but are never automatically rebased onto a newer PPTX.
- A queued patch is not a successful save. Its status is checked after reload;
  failures retain the draft. Mutation and duplicate submission are blocked while pending.
- Download uses the current server artifact, not the local canvas. The UI explicitly
  warns when the local draft is not included.
- Undo/redo includes the logo choice. Undo history is intentionally session-local;
  only the resulting draft survives reload or variant switching.
- Browser storage is not a server backup or collaborative editing system. Clearing
  site data removes drafts. Storage failures are shown and keep an in-memory fallback.
- Uploaded image file IDs survive reload, but blob preview URLs do not. The UI warns
  when an image preview is missing; applying still uses the server-side file ID.

## Validation

From the frontend directory, using the package manager from package.json:

```sh
pnpm typecheck
pnpm build:mock
pnpm exec playwright test --workers=3 --grep 'визуальный редактор|сохранность черновика|draft storage'
```

These tests use the production static export and MSW, not a live PPTX renderer.
They cover variant/revision isolation, reload, rejected requests, failed queued jobs,
latest-revision downloads, read-only old drafts, undo/redo, slide ordering, image and
background edits, and laptop-width slide navigation. Full round-trip fidelity on the
three real project templates is a separate integration check, not established by mocks.