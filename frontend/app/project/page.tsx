import { Suspense } from "react";

import { ProjectClient } from "./ProjectClient";

// Статический экспорт: проекты создаются в браузере, поэтому страница одна,
// а идентификатор берётся из query-параметра на клиенте.
export default function ProjectPage() {
  return (
    <Suspense fallback={null}>
      <ProjectClient />
    </Suspense>
  );
}
