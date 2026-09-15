import { Suspense } from "react";

import { WorkspaceClient } from "./WorkspaceClient";

// Статический экспорт: задания имеют UUID, неизвестные при сборке, поэтому страница одна,
// а идентификатор берётся из query-параметра на клиенте.
export default function WorkspacePage() {
  return (
    <Suspense fallback={null}>
      <WorkspaceClient />
    </Suspense>
  );
}
