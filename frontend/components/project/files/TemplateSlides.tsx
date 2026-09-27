"use client";

import { Group, Loader, Text } from "@mantine/core";

import { api, type TemplateDetail } from "@/lib/api/client";
import { dragProps } from "@/lib/state/drag";

/** Слайд шаблона в сетке: номер в файле (с единицы) и его рендер. */
export type TemplateSlide = { index: number; preview: string };

/**
 * Готовые слайды шаблона: образцы содержания из профиля — без инструкций по оформлению,
 * каталогов ресурсов и пустых. У профиля без классификации — все отрендеренные слайды файла.
 */
export function templateSlides(detail: TemplateDetail | null): TemplateSlide[] {
  const samples = detail?.profile?.sample_slides ?? [];
  if (samples.length) {
    return samples
      .filter((s) => s.classification === "content_sample" && s.preview_path)
      .map((s) => ({ index: s.slide_index, preview: s.preview_path as string }));
  }
  return (detail?.previews ?? [])
    .map((name) => ({ name, match: /(^|\/)slide-(\d+)\.png$/.exec(name) }))
    .flatMap(({ name, match }) => (match ? [{ index: Number(match[2]), preview: name }] : []))
    .sort((a, b) => a.index - b.index);
}

/**
 * Вкладка «Слайды»: готовые слайды шаблона проекта. Слайд перетаскивают в открытую
 * презентацию — он встаёт копией после текущего слайда; по щелчку открывается рендер.
 */
export function TemplateSlides({ templateId, detail }: { templateId: string | null; detail: TemplateDetail | null }) {
  if (!templateId) return <Text size="sm" c="dimmed" py="md" data-testid="template-slides-empty">Шаблон не выбран. Выберите его вверху справа — здесь появятся его готовые слайды.</Text>;
  if (!detail || detail.status === "queued" || detail.status === "running") return <Group gap="xs" py="md"><Loader size="xs" /><Text size="sm" c="dimmed">Шаблон разбирается…</Text></Group>;
  if (detail.status === "failed") return <Text size="sm" c="dimmed" py="md">Разбор шаблона не удался: слайдов нет.</Text>;
  const slides = templateSlides(detail);
  if (!slides.length) return <Text size="sm" c="dimmed" py="md" data-testid="template-slides-empty">В шаблоне нет готовых слайдов.</Text>;

  return (
    <div className="template-slides" data-testid="template-slides">
      <Text size="xs" c="dimmed" py={10}>Перетащите слайд в презентацию — он встанет после текущего слайда.</Text>
      <div className="file-grid file-grid-slides">
        {slides.map((s) => {
          const url = api.templates.assetUrl(templateId, s.preview);
          const name = `Слайд ${s.index} шаблона`;
          return (
            <div key={s.index} className="file-card slide-card" data-testid={`template-slide-${s.index}`} title={`${name} — перетащите в презентацию`}
              {...dragProps({ template_id: templateId, slide: s.index, name })}>
              <a className="file-thumb" href={url} target="_blank" rel="noreferrer" draggable={false} aria-label={`Открыть слайд ${s.index} шаблона`}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={url} alt="" loading="lazy" draggable={false} />
              </a>
              <div className="file-meta">
                <Text size="xs" truncate>Слайд {s.index}</Text>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
