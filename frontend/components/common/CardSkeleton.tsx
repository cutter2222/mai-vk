import { Skeleton } from "@mantine/core";

/**
 * Заглушка карточки сетки на время загрузки списка: та же анатомия, что у карточки проекта
 * или шаблона (миниатюра 16:9 и три строки подписи той же высоты), поэтому сетка не прыгает,
 * когда приходят данные, и не мигает пустотой до них. В одну колонку на телефоне ряд никого
 * не растягивает, поэтому высоты строк повторяют подпись карточки точно.
 */
export function CardSkeleton({ kind = "project" }: { kind?: "project" | "template" }) {
  // Строки подписи: название (19), статус с отступом 5, дата с отступом 2 у проекта и 12 у шаблона.
  const rows: Array<{ height: number; top: number; width: string; bar: number }> = [
    { height: 19, top: 0, width: "70%", bar: 13 },
    { height: 19, top: 5, width: "55%", bar: 11 },
    { height: 17, top: kind === "template" ? 12 : 2, width: "35%", bar: 11 },
  ];
  return (
    <div className="grid-card grid-card-skeleton" aria-hidden data-testid="card-skeleton">
      <div className="grid-card-thumb"><Skeleton height="100%" radius={0} /></div>
      <div className="grid-card-body">
        {rows.map((row, index) => (
          <div key={index} style={{ height: row.height, marginTop: row.top, display: "flex", alignItems: "center" }}>
            <Skeleton height={row.bar} width={row.width} radius="sm" />
          </div>
        ))}
      </div>
    </div>
  );
}
