"use client";

import { ActionIcon, Badge, Button, Group, Loader, SegmentedControl, SimpleGrid, Stack, Text, Tooltip } from "@mantine/core";
import { IconArrowBackUp, IconArrowForwardUp, IconColorSwatch, IconEraser, IconListCheck, IconPencil, IconSitemap, IconStar, IconStarFilled, IconTextPlus, IconX } from "@tabler/icons-react";
import { useEffect, useState } from "react";

import type { Outline } from "@/components/common/SlideImage";
import { PropertiesPanel } from "@/components/project/editor/PropertiesPanel";
import { SlideCanvas } from "@/components/project/editor/SlideCanvas";
import { HowBuiltPanel } from "@/components/workspace/HowBuiltPanel";
import { VariantCard } from "@/components/workspace/VariantCard";
import { api, type TemplateDetail } from "@/lib/api/client";
import type { ComposedDeck, ContentPackage } from "@/lib/api/types";
import { isHollow } from "@/lib/editor/hit";
import { objectLabel } from "@/lib/editor/overrides";
import { formatMs, STAGE_LABELS, VARIANT_LABELS } from "@/lib/format";
import { useElapsed } from "@/lib/hooks/useElapsed";
import type { GenerationSession } from "@/lib/hooks/useGenerationSession";
import type { SlideEditor } from "@/lib/hooks/useSlideEditor";

import { SlideViewer, type ViewerSlide } from "./SlideViewer";

interface Props {
  session: GenerationSession;
  editor: SlideEditor;
  templateDetail: TemplateDetail | null;
  pkg: ContentPackage | null | undefined;
  projectId: string | null;
  chosenVariant: string | null;
  onChoose: (variantId: string | null) => void;
  /** Показать аудит: лента слева переключается на метку «Аудит». */
  onShowAudit: () => void;
}

const VARIANT_DOT: Record<string, string> = { pending: "gray", running: "blue", ready: "green", needs_review: "yellow", failed: "red" };

/**
 * Шрифты колоды — в страницу: холст рисует текст браузером, а гарнитуры шаблона в системе
 * пользователя обычно нет, и «Редактировать» меняло вид слайда. Файлы приложены к ревизии
 * тем же резолвером, которым мерилась вместимость, поэтому на холсте стоит тот же шрифт,
 * по которому считалась вёрстка.
 */
function useDeckFonts(jobId: string | null, deck: ComposedDeck | null): void {
  const faces = (deck?.fonts ?? [])
    .flatMap((font) =>
      (font.files ?? []).map((file) => ({ family: font.family, weight: file.weight, artifact: file.artifact, format: file.format ?? "truetype" })),
    )
    .filter((f) => f.artifact);
  const key = jobId ? `${jobId}|${faces.map((f) => `${f.family}:${f.weight}:${f.artifact}`).join(",")}` : "";
  useEffect(() => {
    if (!jobId || faces.length === 0) return;
    const style = document.createElement("style");
    style.dataset.deckFonts = jobId;
    style.textContent = faces
      .map((f) => `@font-face{font-family:"${f.family}";font-weight:${f.weight};font-display:block;src:url("${api.generations.artifactUrl(jobId, f.artifact)}") format("${f.format}")}`)
      .join("");
    document.head.appendChild(style);
    return () => style.remove();
    // Набор граней описан ключом: пересобирать <style> на каждый рендер незачем.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
}

/** Слайды сгенерированной презентации: переключение вариантов, просмотр по одному или рядом, рамки аудита, редактор. */
export function GenerationPreview({ session, editor, templateDetail, pkg, projectId, chosenVariant, onChoose, onShowAudit }: Props) {
  const { jobId, result, variant } = session;
  const elapsed = useElapsed(result?.created_at, session.terminal ? (result?.finished_at ?? result?.created_at) : null);
  // Свойства слайда (фон) — тот же ящик, что и свойства объекта, но открывается кнопкой:
  // без выбранного объекта ящик закрыт, иначе правка снова начиналась бы с полей ни о чём.
  const [slideProps, setSlideProps] = useState(false);
  useDeckFonts(jobId, editor.deck);

  // Лента считается до раннего выхода: ниже есть эффект, а хуки должны вызываться в одном порядке.
  const thumbs = jobId && result ? (variant?.artifacts?.thumbnails ?? []) : [];
  const issueSlides = new Set(session.audit.data?.issues.map((i) => i.slide_index) ?? []);
  const thumbUrl = (name: string) =>
    api.generations.artifactUrl(jobId ?? "", session.viewRevision !== session.currentRevision ? name.replace(`/r${session.currentRevision}/`, `/r${session.viewRevision}/`) : name);
  const thumbByDeckIndex = (deckIndex: number) => {
    const t = thumbs.find((x) => x.slide_index === deckIndex);
    return t ? thumbUrl(t.name) : undefined;
  };
  const deck = editor.deck;
  // Порядок ленты — черновой порядок редактора, если описание колоды загружено; иначе миниатюры ревизии.
  const slides: ViewerSlide[] = deck
    ? editor.slides.map((s, position) => ({
        key: s.slide_id,
        deckIndex: s.index,
        src: thumbByDeckIndex(s.index),
        label: `Слайд ${s.index + 1}`,
        flagged: issueSlides.has(s.index),
        edited: editor.changedSlides.some((c) => c.slide_id === s.slide_id) || (editor.orderChanged && deck.slides[position]?.slide_id !== s.slide_id),
      }))
    : thumbs.map((t) => ({ key: t.name, deckIndex: t.slide_index, src: thumbUrl(t.name), label: `Слайд ${t.slide_index + 1}`, flagged: issueSlides.has(t.slide_index) }));
  // Выбранный слайд хранится номером в колоде, а лента живёт местами: после перестановки в
  // черновике место и номер расходятся, и раньше рамки аудита, миниатюра в чипе правки и
  // холст редактора начинали показывать разные слайды.
  const found = slides.findIndex((s) => s.deckIndex === session.slideIndex);
  // Слайда с таким номером в варианте нет (короче предыдущего, ревизия пересобрана): берём
  // ближайший существующий, а не первый, и сразу выравниваем выбор, чтобы правка ушла туда же.
  const position = found >= 0 ? found : Math.min(Math.max(session.slideIndex, 0), Math.max(slides.length - 1, 0));
  const fallbackDeckIndex = found < 0 && slides.length > 0 ? slides[position].deckIndex : null;
  const { selectSlide, slideIndex } = session;
  useEffect(() => {
    if (fallbackDeckIndex !== null && fallbackDeckIndex !== slideIndex) selectSlide(fallbackDeckIndex);
  }, [fallbackDeckIndex, slideIndex, selectSlide]);

  if (!jobId || !result) return null;

  const selectByPosition = (pos: number) => {
    const target = slides[pos] ?? slides[slides.length - 1];
    if (target) session.selectSlide(target.deckIndex);
  };
  const running = !session.terminal;
  const multi = result.variants.length > 1;
  // Пропорция слайда — из описания колоды: у шаблона не 16:9 рамки находок иначе считались бы
  // от другого прямоугольника, чем показанная картинка.
  const ratio =
    deck?.slide_size?.width_emu && deck.slide_size.height_emu
      ? deck.slide_size.width_emu / deck.slide_size.height_emu
      : undefined;
  const isChosen = Boolean(variant && chosenVariant === variant.variant_id);
  const current = editor.currentSlide;
  const outlines: Outline[] =
    editor.available && current && !editor.editing
      ? current.objects
          .filter((o) => o.kind !== "group" && o.kind !== "connector" && o.kind !== "other")
          .map((o) => ({ id: o.object_id, bbox: o.bbox, label: objectLabel(o), z: o.z_order, hollow: isHollow(o) }))
      : [];
  const layoutUrl =
    current && templateDetail?.previews.includes(`previews/layout-${current.layout_id}.png`) && templateDetail.profile
      ? api.templates.assetUrl(templateDetail.profile.template_id, `previews/layout-${current.layout_id}.png`)
      : null;
  const pattern = templateDetail?.profile?.patterns.find((p) => p.pattern_id === current?.pattern_id);
  const fallbackBackground = pattern?.tone?.background === "dark" ? "#1d1f25" : "#ffffff";
  const mediaUrl = (assetId: string) => {
    const asset = deck?.assets?.find((a) => a.asset_id === assetId);
    return asset?.artifact ? api.generations.artifactUrl(jobId, asset.artifact) : undefined;
  };
  const openEditor = (objectId: string | null) => {
    editor.setEditing(true);
    editor.selectObject(objectId);
    if (objectId) setSlideProps(false);
  };
  const leaveEditor = () => {
    editor.setEditing(false);
    setSlideProps(false);
  };
  // Выбор на холсте: объект открывает ящик свойств, пустое место закрывает правку целиком —
  // и ящик, и холст уходят одним движением, слайд возвращается к прежнему размеру.
  // Нажатие по объекту на холсте неприменённого черновика снова включает правку.
  const selectOnCanvas = (objectId: string | null) => {
    if (objectId) {
      if (!editor.editing) editor.setEditing(true);
      editor.selectObject(objectId);
      setSlideProps(false);
      return;
    }
    leaveEditor();
  };
  // Холст показывает слайд и после выхода из правки, пока черновик этого слайда не применён:
  // картинка ревизии ещё старая, и «Готово» выглядело так, будто набранный текст пропал.
  const showCanvas = Boolean(deck && editor.previewSlide && (editor.editing || editor.slideDirty));
  // Ящик правки выезжает под выбранный объект и уезжает, когда выделение снято нажатием на
  // пустое место: вход в режим правки сам по себе полей не показывает.
  const drawerOpen = editor.editing && (Boolean(editor.selectedObjectId) || slideProps);

  return (
    <>
      <div className="preview-toolbar">
        {/* Переключатель вариантов и сравнение появляются, только когда вариантов больше одного:
            у готовой презентации вариант один, и оба элемента были бы выбором без выбора. */}
        <Group gap="xs" wrap="nowrap" style={{ minWidth: 0 }}>
          {multi ? (
            <SegmentedControl
              size="xs"
              value={variant?.variant_id ?? ""}
              onChange={session.setSelectedVariant}
              data={result.variants.map((v) => ({
                value: v.variant_id,
                label: (
                  <Group gap={6} wrap="nowrap">
                    <span style={{ width: 7, height: 7, borderRadius: "50%", background: `var(--mantine-color-${VARIANT_DOT[v.status] ?? "gray"}-6)` }} />
                    {VARIANT_LABELS[v.variant_id] ?? v.variant_id}
                  </Group>
                ),
              }))}
              data-testid="variant-switch"
            />
          ) : variant ? (
            <Group gap={7} wrap="nowrap" data-testid="variant-single">
              <span style={{ width: 7, height: 7, borderRadius: "50%", background: `var(--mantine-color-${VARIANT_DOT[variant.status] ?? "gray"}-6)`, flex: "0 0 auto" }} />
              <Text size="sm" fw={500} truncate>{VARIANT_LABELS[variant.variant_id] ?? variant.variant_id}</Text>
            </Group>
          ) : null}
          {variant && multi && (
            <Tooltip label={isChosen ? "Выбранный вариант для демонстрации" : "Отметить как выбранный для демонстрации"}>
              <ActionIcon variant="subtle" color={isChosen ? "yellow" : "gray"} onClick={() => onChoose(isChosen ? null : variant.variant_id)} aria-label="Выбрать вариант" data-testid={`choose-${variant.variant_id}`}>
                {isChosen ? <IconStarFilled size={18} /> : <IconStar size={18} />}
              </ActionIcon>
            </Tooltip>
          )}
        </Group>
        <Group gap="xs" wrap="nowrap">
          {multi && (
            <SegmentedControl size="xs" value={session.layout} onChange={(v) => session.setLayout(v as "single" | "side")} data={[{ value: "single", label: "Один вариант" }, { value: "side", label: "Сравнить" }]} data-testid="layout-switch" />
          )}
          {editor.available && (
            <Button
              size="xs"
              variant={editor.editing ? "filled" : "default"}
              leftSection={<IconPencil size={14} />}
              onClick={() => (editor.editing ? leaveEditor() : openEditor(null))}
              data-testid="toggle-editor"
            >
              {editor.editing ? "Готово" : "Редактировать"}
            </Button>
          )}
          {variant?.audit && variant.audit.status !== "pending" && (
            <Button
              size="xs"
              variant="default"
              leftSection={<IconListCheck size={14} />}
              onClick={() => {
                session.setLayout("single");
                leaveEditor();
                onShowAudit();
              }}
              data-testid="toggle-audit"
            >
              Аудит{variant.audit.issues_total ? ` · ${variant.audit.issues_total}` : ""}
            </Button>
          )}
        </Group>
      </div>

      {running && (
        <div className="preview-progress" data-testid="preview-progress">
          <Group gap={8} wrap="nowrap" style={{ minWidth: 0 }}><Loader size={12} /><Text size="xs" truncate>{result.progress?.message ?? STAGE_LABELS[result.stage]}</Text></Group>
          <Text size="xs" fw={600} style={{ whiteSpace: "nowrap" }}>{formatMs(elapsed)}</Text>
        </div>
      )}

      {session.layout === "side" ? (
        <div className="preview-scroll">
          <SimpleGrid cols={{ base: 1, md: result.variants.length }} spacing="md" data-testid="variants-grid">
            {result.variants.map((v) => (
              <VariantCard
                key={v.variant_id}
                jobId={jobId}
                variant={v}
                slideIndex={session.slideIndex}
                selected={v.variant_id === session.selectedVariant}
                chosen={v.variant_id === chosenVariant}
                onSelect={() => session.setSelectedVariant(v.variant_id)}
                onChoose={() => onChoose(chosenVariant === v.variant_id ? null : v.variant_id)}
                onSlideChange={session.selectSlide}
              />
            ))}
          </SimpleGrid>
        </div>
      ) : variant && slides.length === 0 ? (
        <div className="preview-empty">
          <Stack align="center" gap={6} maw={420}>
            <Text fw={600}>{variant.status === "failed" ? "Вариант не собран" : "Слайды появятся, как только вариант будет собран"}</Text>
            <Text size="sm" c="dimmed" ta="center">{variant.error?.message ?? variant.rationale}</Text>
          </Stack>
        </div>
      ) : variant ? (
        <SlideViewer
          slides={slides}
          ratio={ratio}
          index={position}
          onIndex={selectByPosition}
          overlays={editor.editing ? [] : session.overlays}
          activeOverlay={session.activeIssue}
          onOverlayClick={session.setActiveIssue}
          outlines={outlines}
          onOutlineClick={(id) => openEditor(id)}
          editing={editor.editing}
          onReorder={editor.available ? editor.reorder : undefined}
          stage={
            showCanvas && deck && editor.previewSlide ? (
              <SlideCanvas
                deck={deck}
                slide={editor.previewSlide}
                layoutUrl={layoutUrl}
                thumbUrl={current ? thumbByDeckIndex(current.index) : undefined}
                mediaUrl={mediaUrl}
                selectedObjectId={editor.editing ? editor.selectedObjectId : null}
                onSelect={selectOnCanvas}
                editable={editor.editing}
                onDelete={editor.deleteObject}
                onGeometry={(objectId, bbox) => {
                  const obj = current?.objects.find((o) => o.object_id === objectId);
                  editor.setOp({ op: "geometry", target: { object_id: objectId, ...(obj?.source_object_id ? { source_object_id: obj.source_object_id } : {}) }, geometry: { bbox } });
                }}
                fallbackBackground={fallbackBackground}
              />
            ) : undefined
          }
          caption={
            <>
              {/* Название варианта уже стоит в верхней строке. Здесь оно повторялось и съедало
                  место, поэтому остаётся только там, где вариантов несколько. */}
              {multi && <Text size="sm" fw={500}>{VARIANT_LABELS[variant.variant_id] ?? variant.variant_id}</Text>}
              <Text size="xs" c="dimmed">ревизия {session.viewRevision}</Text>
              {session.viewRevision !== session.currentRevision && <Badge size="xs" color="orange" variant="light">устаревшая</Badge>}
              {variant.status === "running" && <Badge size="xs" color="blue" variant="light" data-testid="preview-provisional">предварительный показ · сборка идёт</Badge>}
              {/* Один значок вместо двух: сколько правок в черновике и, если правка уже
                  закрыта, что показан именно черновик. */}
              {editor.dirty && (
                <Badge
                  size="xs"
                  color={editor.slideDirty && !editor.editing ? "orange" : "ink"}
                  variant="light"
                  data-testid="draft-badge"
                >
                  черновик: {editor.draftCount}
                  {editor.slideDirty && !editor.editing ? " · не применён" : ""}
                </Badge>
              )}
              {/* При открытой панели аудита сводка находок не дублируется в подписи: там она
                  подробнее, а в узкой строке обрезалась до «80 …». */}
              {variant.audit && variant.audit.status !== "pending" && !editor.editing && (
                <Badge size="xs" variant="light" color={variant.audit.status === "running" ? "blue" : variant.audit.issues_total ? "yellow" : variant.audit.coverage_complete ? "green" : "gray"}>
                  {variant.audit.status === "running" ? "аудит идёт" : `${variant.audit.issues_total} находок${variant.audit.coverage_complete ? "" : " · аудит неполный"}`}
                </Badge>
              )}
            </>
          }
          actions={
            <>
              {/* Инструменты правки — иконки с подсказками: в строке под слайдом их три,
                  и подписями они занимали половину ширины. */}
              {editor.editing && (
                <>
                  <Tooltip label="Добавить надпись">
                    <ActionIcon variant="subtle" color="gray" size="md" onClick={editor.addText} aria-label="Добавить надпись" data-testid="editor-add-text">
                      <IconTextPlus size={16} />
                    </ActionIcon>
                  </Tooltip>
                  <Tooltip label="Фон слайда">
                    <ActionIcon
                      variant={slideProps ? "light" : "subtle"}
                      color={slideProps ? "blue" : "gray"}
                      size="md"
                      onClick={() => {
                        if (slideProps) {
                          setSlideProps(false);
                          return;
                        }
                        editor.selectObject(null);
                        setSlideProps(true);
                      }}
                      aria-label="Фон слайда"
                      data-testid="toggle-slide-props"
                    >
                      <IconColorSwatch size={16} />
                    </ActionIcon>
                  </Tooltip>
                  {/* Знак шаблона: чужой логотип делает шаблон непригодным для другого
                      подразделения, а снимать его со всех слайдов вручную никто не станет. */}
                  {editor.logoCount > 0 && (
                    <Tooltip label={editor.logoDropped ? "Вернуть знак шаблона" : "Убрать знак шаблона со всей колоды"}>
                      <ActionIcon
                        variant={editor.logoDropped ? "light" : "subtle"}
                        color={editor.logoDropped ? "blue" : "gray"}
                        size="md"
                        onClick={editor.toggleLogo}
                        aria-label={editor.logoDropped ? "Вернуть знак шаблона" : "Убрать знак шаблона"}
                        data-testid="editor-drop-logos"
                      >
                        <IconEraser size={16} />
                      </ActionIcon>
                    </Tooltip>
                  )}
                  <span className="bar-sep" />
                </>
              )}
              {/* Шаги черновика: те же Ctrl+Z и Ctrl+Shift+Z, но видимые. Кнопки остаются и после
                  выхода из правки — черновик жив, пока его не применили или не отменили. */}
              {editor.available && (editor.editing || editor.dirty) && (
                <>
                  <ActionIcon
                    variant="subtle"
                    color="gray"
                    size="md"
                    disabled={!editor.canUndo}
                    onClick={editor.undo}
                    title="Шаг назад · Ctrl+Z"
                    aria-label="Шаг назад"
                    data-testid="editor-undo"
                  >
                    <IconArrowBackUp size={16} />
                  </ActionIcon>
                  <ActionIcon
                    variant="subtle"
                    color="gray"
                    size="md"
                    disabled={!editor.canRedo}
                    onClick={editor.redo}
                    title="Шаг вперёд · Ctrl+Shift+Z"
                    aria-label="Шаг вперёд"
                    data-testid="editor-redo"
                  >
                    <IconArrowForwardUp size={16} />
                  </ActionIcon>
                  <span className="bar-sep" />
                </>
              )}
              {/* Применение черновика словами, а не значком: это решение, а не инструмент. */}
              {editor.dirty && !drawerOpen && (
                <>
                  <Button variant="subtle" color="gray" size="compact-xs" onClick={editor.discard} data-testid="editor-cancel-bar">
                    Отменить
                  </Button>
                  <Button size="compact-xs" loading={editor.applying} onClick={() => void editor.apply()} data-testid="editor-apply-bar">
                    Применить
                  </Button>
                  <span className="bar-sep" />
                </>
              )}
              <Tooltip label="Как собран слайд">
                <ActionIcon
                  variant={session.showHowBuilt ? "light" : "subtle"}
                  color={session.showHowBuilt ? "blue" : "gray"}
                  size="md"
                  onClick={() => session.setShowHowBuilt(!session.showHowBuilt)}
                  aria-label="Как собран слайд"
                  data-testid="toggle-how-built"
                >
                  <IconSitemap size={16} />
                </ActionIcon>
              </Tooltip>
            </>
          }
          aside={
            drawerOpen ? (
              <div className="editor-drawer" data-testid="editor-drawer">
                <Group justify="space-between" mb="xs">
                  <Text fw={600}>{editor.selectedObjectId ? "Объект" : "Слайд"}</Text>
                  <ActionIcon
                    variant="subtle"
                    color="gray"
                    size="sm"
                    onClick={() => {
                      editor.selectObject(null);
                      setSlideProps(false);
                    }}
                    aria-label="Закрыть панель правки"
                    data-testid="editor-done"
                  >
                    <IconX size={14} />
                  </ActionIcon>
                </Group>
                {editor.deckError ? (
                  <Text size="xs" c="red">Описание колоды не загружено: {editor.deckError}</Text>
                ) : (
                  <PropertiesPanel editor={editor} profile={templateDetail?.profile} pkg={pkg} projectId={projectId} />
                )}
                {/* Подсказки по клавишам стояли в подсказке кнопки «как собран» — там их никто
                    не искал. Здесь они видны ровно тогда, когда правят слайд. */}
                <Text size="xs" c="dimmed" mt="sm">↑ ↓ листают слайды · Esc закрывает правку · Ctrl+Z отменяет шаг · Delete убирает объект</Text>
              </div>
            ) : null
          }
        >
          <Stack gap="md">
            {!editor.editing && variant.rationale && (
              <Text size="xs" c="dimmed" lineClamp={2} title={variant.rationale}>{variant.rationale}</Text>
            )}
            {session.showHowBuilt && <HowBuiltPanel jobId={jobId} result={result} variantId={variant.variant_id} slideIndex={current?.index ?? session.slideIndex} />}
          </Stack>
        </SlideViewer>
      ) : null}
    </>
  );
}
