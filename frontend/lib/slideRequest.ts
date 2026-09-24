/**
 * Просьба к слайду по номеру: «на слайде 3 сократи текст», «3-й слайд — крупнее заголовок»,
 * «на третьем слайде …». В редакторе ONLYOFFICE слайд не выделить для чата — номер берётся
 * из самой фразы. Вопрос («что на слайде 3?») и счёт слайдов («до 10 слайдов») просьбой
 * к слайду не считаются: такие сообщения уходят ассистенту как обычно.
 */
const ORDINALS = ["перв", "втор", "трет", "четверт", "пят", "шест", "седьм", "восьм", "девят", "десят"];

/** Вопрос, а не просьба: «что на слайде 3?», «сколько слайдов», «где таблица». */
export function isQuestion(text: string): boolean {
  const t = text.toLowerCase().replace(/ё/g, "е").trim();
  return /\?\s*$/.test(t) || /^(что|как|почему|зачем|сколько|какой|какая|какие|где|когда|о\s+чем|есть\s+ли)(?![а-я])/.test(t);
}

export function slideRequest(text: string): number | null {
  const t = text.toLowerCase().replace(/ё/g, "е").trim();
  if (!t || isQuestion(t)) return null;
  // «слайд 3», «на слайде 3», «слайд №3»
  const after = t.match(/(?:^|[^а-я])слайд(?:е|а|у|ом)?\s*(?:№\s*)?(\d{1,3})(?!\d)/);
  // «3 слайд», «на 3 слайде», «3-й слайд», «на 3-м слайде»; «10 слайдов» — счёт, не номер
  const before = t.match(/(?:^|[^\d])(\d{1,3})\s*(?:-?\s*(?:й|я|е|м|ом|ем|ой|ий))?\s+слайд(?:е|а|у|ом)?(?![а-я])/);
  const found = after ?? before;
  if (found) {
    const n = Number(found[1]);
    return n >= 1 ? n : null;
  }
  // «на третьем слайде», «пятый слайд»; «пятнадцатый» — не пятый
  for (const m of t.matchAll(/(?:^|[^а-я])([а-я]+)\s+слайд(?:е|а|у|ом)?(?![а-я])/g)) {
    const word = m[1];
    const index = ORDINALS.findIndex((stem) => word.startsWith(stem) && word.length <= stem.length + 4 && !word.includes("надцат"));
    if (index >= 0) return index + 1;
  }
  return null;
}
