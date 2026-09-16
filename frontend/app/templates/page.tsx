import { Suspense } from "react";

import { TemplatesClient } from "./TemplatesClient";

// Статический экспорт: страница одна, идентификатор открытого шаблона — query-параметр на клиенте.
export default function TemplatesPage() {
  return (
    <Suspense fallback={null}>
      <TemplatesClient />
    </Suspense>
  );
}
