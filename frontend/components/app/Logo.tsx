/**
 * Знак сервиса: слайд с композицией — рамка формата 16:9, заголовочная строка и два блока
 * содержания. Ровно то, что делает сервис, поэтому подпись рядом не нужна.
 *
 * Один цвет (акцент), без градиентов и теней: знак стоит рядом с названием проекта и не
 * должен спорить с ним за внимание.
 */
export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 28 28"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden
      focusable="false"
    >
      <rect width="28" height="28" rx="8" fill="var(--accent)" />
      {/* Заголовок слайда */}
      <rect x="7" y="8" width="14" height="2.5" rx="1.25" fill="#fff" />
      {/* Два блока содержания: узкий и широкий — композиция, а не просто строки текста */}
      <rect x="7" y="13" width="6" height="7" rx="2" fill="#fff" fillOpacity="0.55" />
      <rect x="15" y="13" width="6" height="7" rx="2" fill="#fff" />
    </svg>
  );
}
