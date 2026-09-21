"use client";

import { ActionIcon, Autocomplete, Badge, Button, ColorInput, ColorSwatch, Group, NumberInput, SegmentedControl, Stack, Text, Textarea, Tooltip } from "@mantine/core";
import { IconAlignCenter, IconAlignJustified, IconAlignLeft, IconAlignRight, IconBold, IconItalic } from "@tabler/icons-react";
import { useState } from "react";

import type { ContentPackage, Override, TemplateProfile } from "@/lib/api/types";
import { findOverride, objectLabel, type DeckObject } from "@/lib/editor/overrides";
import { offTemplate, type TemplateTokens } from "@/lib/editor/tokens";
import { plural } from "@/lib/format";
import type { SlideEditor } from "@/lib/hooks/useSlideEditor";

import { AssetPicker, type PictureSource } from "./AssetPicker";

interface Props {
  editor: SlideEditor;
  profile: TemplateProfile | null | undefined;
  pkg: ContentPackage | null | undefined;
  projectId: string | null;
}

const KIND_LABELS: Record<string, string> = {
  text: "текст",
  placeholder_empty: "пустой текст",
  picture: "картинка",
  table: "таблица",
  chart: "диаграмма",
  shape: "фигура",
  connector: "линия",
  group: "группа",
  other: "объект",
};

/**
 * Панель свойств под слайдом: текст и оформление выбранного объекта, положение и размер,
 * фон слайда; внизу — применение черновика одной ревизией. Токены шаблона предлагаются
 * первыми, произвольное значение помечается «не из шаблона».
 */
export function PropertiesPanel({ editor, profile, pkg, projectId }: Props) {
  const pickerProps = { templateId: editor.templateId, profile, pkg, projectId };
  const slide = editor.previewSlide;
  const original = editor.currentSlide;
  const obj = slide?.objects.find((o) => o.object_id === editor.selectedObjectId);
  const originalObj = original?.objects.find((o) => o.object_id === editor.selectedObjectId);
  const draftOps = editor.draft.filter((o) => (obj ? o.target?.object_id === obj.object_id : false));
  const edited = draftOps.length > 0 || Boolean(originalObj?.user_overrides?.length);
  const outside = draftOps.flatMap((o) => offTemplate(o, editor.tokens));
  const canText = obj && (obj.kind === "text" || obj.kind === "placeholder_empty" || (obj.kind === "shape" && Boolean(obj.text)));
  const canMove = obj && obj.kind !== "connector" && obj.kind !== "other";

  return (
    <div className="object-panel" data-testid="object-panel">
      <Group justify="space-between" align="flex-start" mb="xs" wrap="wrap">
        <div>
          {obj ? (
            <Group gap={6} wrap="wrap">
              <Text fw={600} size="sm" data-testid="object-title">
                {capitalize(objectLabel(obj))}
              </Text>
              <Badge size="xs" variant="light" color="gray">{KIND_LABELS[obj.kind] ?? obj.kind}</Badge>
              {edited && <Badge size="xs" variant="light" color="ink" data-testid="badge-user-edited">изменено вручную</Badge>}
              {outside.length > 0 && (
                <Tooltip label={outside.join(", ")}>
                  <Badge size="xs" variant="light" color="yellow" data-testid="badge-off-template">не из шаблона</Badge>
                </Tooltip>
              )}
            </Group>
          ) : (
            <Text fw={600} size="sm">Слайд {slide ? editor.slideOrder.indexOf(slide.slide_id) + 1 : ""}</Text>
          )}
          <Text size="xs" c="dimmed" mt={2}>
            {obj ? "Правки видны сразу; точная картинка появится после «Применить»." : "Нажмите объект на слайде, чтобы изменить текст, оформление или положение."}
          </Text>
        </div>
        <Group gap="xs" wrap="nowrap">
          {obj && edited && (
            <Button size="compact-xs" variant="subtle" color="gray" onClick={() => editor.resetObject(obj.object_id)} data-testid="editor-reset-object">
              Сбросить объект
            </Button>
          )}
          {obj && (
            <Button
              size="compact-xs"
              variant="subtle"
              color="red"
              onClick={() => editor.deleteObject(obj.object_id)}
              title="Убрать объект со слайда · Delete"
              data-testid="editor-delete-object"
            >
              Удалить
            </Button>
          )}
        </Group>
      </Group>

      {obj && canText && <TextProperties obj={obj} originalObj={originalObj} editor={editor} tokens={editor.tokens} />}
      {obj && obj.kind === "picture" && <PictureProperties obj={obj} originalObj={originalObj} editor={editor} tokens={editor.tokens} picker={pickerProps} />}
      {obj && canMove && <GeometryFields obj={obj} editor={editor} />}
      {obj && (obj.kind === "table" || obj.kind === "chart") && <Text size="xs" c="dimmed">Здесь можно изменить положение и размер. Данные таблицы или диаграммы редактируются в скачанном PPTX.</Text>}
      {obj && !canText && !canMove && <Text size="xs" c="dimmed">Этот объект правится только через чат.</Text>}
      {!obj && <BackgroundProperties editor={editor} tokens={editor.tokens} picker={pickerProps} />}

      {/* Подвал прилипает к низу колонки: «Применить» всегда на виду, сколько бы полей ни было. */}
      <Group className="object-panel-foot" justify="space-between" wrap="wrap">
        <Text size="xs" c="dimmed" data-testid="editor-draft-count">
          {editor.dirty ? `Черновик: ${editor.draftCount} ${plural(editor.draftCount, "правка", "правки", "правок")}${editor.orderChanged ? ", порядок изменён" : ""}` : "Черновик пуст"}
        </Text>
        <Group gap="xs">
          <Button size="xs" variant="default" disabled={!editor.dirty || !editor.available} onClick={editor.discard} data-testid="editor-cancel">
            Сбросить
          </Button>
          <Button size="xs" disabled={!editor.dirty || !editor.available} loading={editor.applying} onClick={() => void editor.apply()} data-testid="editor-apply">
            Применить
          </Button>
        </Group>
      </Group>
    </div>
  );
}

/**
 * Пять кеглей шкалы вокруг текущего: крайние значения шаблона (48 и 15 в одной строке) вместе
 * не нужны, нужен выбор рядом с тем, что стоит сейчас. Текущий кегль в список попадает всегда.
 */
function nearestSizes(sizes: number[], current: number | undefined | null, limit = 5): number[] {
  if (sizes.length <= limit) return sizes;
  const value = current ?? sizes[Math.floor(sizes.length / 2)];
  const around = [...sizes].sort((a, b) => Math.abs(a - value) - Math.abs(b - value)).slice(0, limit);
  return around.sort((a, b) => b - a);
}

function capitalize(s: string): string {
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}

// ---------- текст и оформление ----------

interface TextProps {
  obj: DeckObject;
  originalObj: DeckObject | undefined;
  editor: SlideEditor;
  tokens: TemplateTokens;
}

function TextProperties({ obj, originalObj, editor, tokens }: TextProps) {
  const target = { object_id: obj.object_id, ...(obj.source_object_id ? { source_object_id: obj.source_object_id } : {}), ...(obj.slot_id ? { slot_id: obj.slot_id } : {}) };
  const styleOp = findOverride(editor.draft, "style", obj.object_id);
  const textOp = findOverride(editor.draft, "text", obj.object_id);
  const font = { ...(originalObj?.text?.computed_style?.font ?? {}), ...(styleOp?.style?.font ?? {}) };
  const align = styleOp?.style?.align ?? originalObj?.text?.paragraphs?.[0]?.align ?? "left";
  const text = textOp?.text ?? obj.text?.plain ?? "";
  const bullets = obj.block_kind === "bullets";

  const setStyle = (patch: NonNullable<Override["style"]>) => {
    const prev = styleOp?.style ?? {};
    const next: NonNullable<Override["style"]> = { ...prev, ...(patch.font ? { font: { ...(prev.font ?? {}), ...patch.font } } : {}), ...(patch.align ? { align: patch.align } : {}) };
    editor.setOp({ op: "style", target, style: next });
  };
  const families = [...new Set([...(font.family ? [font.family] : []), ...tokens.families])];

  return (
    <Stack gap="xs" data-testid="text-properties">
      <Textarea
        label={bullets ? "Пункты списка (каждый с новой строки)" : "Текст"}
        autosize
        minRows={1}
        maxRows={6}
        size="xs"
        value={text}
        onChange={(e) => editor.setOp({ op: "text", target, text: e.currentTarget.value })}
        data-testid="prop-text"
      />
      <Group gap="sm" align="flex-end" wrap="wrap">
        <Autocomplete
          label="Гарнитура"
          size="xs"
          data={families}
          value={font.family ?? ""}
          onChange={(v) => v && setStyle({ font: { family: v } })}
          style={{ width: 180 }}
          data-testid="prop-family"
        />
        <div>
          <Text size="xs" fw={500} mb={4}>Кегль</Text>
          <Group gap={4} wrap="nowrap">
            {/* Шкала шаблона бывает в десяток кеглей, и строка из восьми кнопок читалась как
                набор цифр. Показываются пять ближайших к текущему: остальные набираются полем. */}
            <Group gap={2} wrap="nowrap" data-testid="prop-size-scale">
              {nearestSizes(tokens.sizes, font.size_pt).map((s) => (
                <Button key={s} size="compact-xs" variant={font.size_pt === s ? "filled" : "default"} onClick={() => setStyle({ font: { size_pt: s } })} data-testid={`prop-size-${s}`}>
                  {s}
                </Button>
              ))}
            </Group>
            <NumberInput
              size="xs"
              min={6}
              max={120}
              step={1}
              value={font.size_pt ?? ""}
              onChange={(v) => typeof v === "number" && v >= 6 && v <= 120 && setStyle({ font: { size_pt: v } })}
              style={{ width: 76 }}
              data-testid="prop-size"
            />
          </Group>
        </div>
        <Group gap={4} wrap="nowrap">
          <Tooltip label="Жирный">
            <ActionIcon variant={font.bold ? "filled" : "default"} size="input-xs" onClick={() => setStyle({ font: { bold: !font.bold } })} aria-label="Жирный" data-testid="prop-bold">
              <IconBold size={14} />
            </ActionIcon>
          </Tooltip>
          <Tooltip label="Курсив">
            <ActionIcon variant={font.italic ? "filled" : "default"} size="input-xs" onClick={() => setStyle({ font: { italic: !font.italic } })} aria-label="Курсив" data-testid="prop-italic">
              <IconItalic size={14} />
            </ActionIcon>
          </Tooltip>
        </Group>
        <SegmentedControl
          size="xs"
          value={align}
          onChange={(v) => setStyle({ align: v as NonNullable<Override["style"]>["align"] })}
          data={[
            { value: "left", label: <IconAlignLeft size={14} data-testid="prop-align-left" /> },
            { value: "center", label: <IconAlignCenter size={14} data-testid="prop-align-center" /> },
            { value: "right", label: <IconAlignRight size={14} data-testid="prop-align-right" /> },
            { value: "justify", label: <IconAlignJustified size={14} data-testid="prop-align-justify" /> },
          ]}
          data-testid="prop-align"
        />
      </Group>
      <ColorField label="Цвет текста" value={font.color} tokens={tokens} onChange={(hex) => setStyle({ font: { color: hex } })} testId="prop-color" />
    </Stack>
  );
}

// ---------- цвет с палитрой шаблона ----------

interface ColorProps {
  label: string;
  value: string | undefined;
  tokens: TemplateTokens;
  onChange: (hex: string) => void;
  testId: string;
}

export function ColorField({ label, value, tokens, onChange, testId }: ColorProps) {
  return (
    <Group gap="sm" align="flex-end" wrap="wrap">
      <ColorInput
        label={label}
        size="xs"
        format="hex"
        value={value ?? ""}
        onChangeEnd={(v) => /^#[0-9a-fA-F]{6}$/.test(v) && onChange(v.toUpperCase())}
        swatches={tokens.colors.map((c) => c.hex)}
        swatchesPerRow={10}
        withEyeDropper={false}
        style={{ width: 150 }}
        data-testid={testId}
      />
      <Group gap={4} pb={4} data-testid={`${testId}-palette`}>
        {tokens.colors.slice(0, 12).map((c) => (
          <Tooltip key={c.hex} label={c.role ? `${c.hex} · ${c.role}` : c.hex}>
            <ColorSwatch
              color={c.hex}
              size={18}
              radius="sm"
              component="button"
              type="button"
              onClick={() => onChange(c.hex)}
              style={{ cursor: "pointer", outline: value?.toUpperCase() === c.hex ? "2px solid var(--mantine-color-blue-6)" : "1px solid var(--mantine-color-gray-3)", outlineOffset: 1 }}
              data-testid={`${testId}-swatch-${c.hex.slice(1)}`}
              aria-label={c.hex}
            />
          </Tooltip>
        ))}
      </Group>
    </Group>
  );
}

// ---------- положение и размер ----------

function GeometryFields({ obj, editor }: { obj: DeckObject; editor: SlideEditor }) {
  const target = { object_id: obj.object_id, ...(obj.source_object_id ? { source_object_id: obj.source_object_id } : {}) };
  const box = obj.bbox;
  const set = (key: keyof typeof box, percent: number | string) => {
    if (typeof percent !== "number") return;
    editor.setOp({ op: "geometry", target, geometry: { bbox: { ...box, [key]: Math.round(percent * 100) / 10000 } } });
  };
  const field = (key: keyof typeof box, label: string) => (
    <NumberInput
      key={key}
      label={label}
      size="xs"
      value={Math.round(box[key] * 1000) / 10}
      onChange={(v) => set(key, v)}
      min={key === "width" || key === "height" ? 0.5 : -50}
      max={150}
      step={0.5}
      decimalScale={1}
      suffix=" %"
      style={{ width: 96 }}
      data-testid={`prop-${{ x: "x", y: "y", width: "w", height: "h" }[key]}`}
    />
  );
  return (
    <Group gap="sm" mt="xs" wrap="wrap" data-testid="geometry-fields">
      {field("x", "Слева")}
      {field("y", "Сверху")}
      {field("width", "Ширина")}
      {field("height", "Высота")}
      <Text size="xs" c="dimmed" pb={6}>в процентах от слайда; перетаскивание и стрелки на холсте делают то же</Text>
    </Group>
  );
}

// ---------- картинка или иконка ----------

interface PickerProps {
  templateId: string | null;
  profile: TemplateProfile | null | undefined;
  pkg: ContentPackage | null | undefined;
  projectId: string | null;
}

const SOURCE_LABELS: Record<string, string> = { template: "из шаблона", package: "из материалов", file: "своя картинка" };

function PictureProperties({ obj, originalObj, editor, tokens, picker }: { obj: DeckObject; originalObj: DeckObject | undefined; editor: SlideEditor; tokens: TemplateTokens; picker: PickerProps }) {
  const [open, setOpen] = useState(false);
  const target = { object_id: obj.object_id, ...(obj.source_object_id ? { source_object_id: obj.source_object_id } : {}), ...(obj.slot_id ? { slot_id: obj.slot_id } : {}) };
  const op = findOverride(editor.draft, "picture", obj.object_id);
  const isIcon = obj.slot_kind === "icon" || (originalObj?.picture?.origin === "template" && (originalObj.bbox.width < 0.12 && originalObj.bbox.height < 0.2));
  const source = op?.picture?.source;
  const current = source ? `${SOURCE_LABELS[source.kind] ?? source.kind}${source.name ? ` (${source.name})` : ""}` : originalObj?.picture?.origin === "content" ? "из материалов" : "из шаблона";
  const fit = op?.picture?.fit ?? (originalObj?.picture?.fit === "cover" ? "cover" : "contain");
  const templateAsset = source?.kind === "template" ? picker.profile?.assets.find((a) => a.asset_id === source.asset_id) : undefined;
  const canRecolor = Boolean(source && source.kind === "template" && templateAsset && (templateAsset.kind === "icon" || templateAsset.kind === "logo"));
  const setPicture = (patch: Partial<NonNullable<Override["picture"]>>) => {
    const prev = op?.picture;
    const next = { ...(prev ?? {}), ...patch };
    if (!next.source) return;
    editor.setOp({ op: "picture", target, picture: { source: next.source, ...(next.fit ? { fit: next.fit } : {}), ...(next.color ? { color: next.color } : {}) } });
  };
  const onPick = (src: PictureSource, url: string) => {
    if (src.kind === "file" && src.file_id) editor.registerFileUrl(src.file_id, url);
    editor.setOp({ op: "picture", target, picture: { source: src, fit: isIcon ? "contain" : "cover" } });
    setOpen(false);
  };
  return (
    <Stack gap="xs" data-testid="picture-properties">
      <Group gap="sm" align="center" wrap="wrap">
        <Text size="xs">
          {isIcon ? "Иконка" : "Картинка"} {current}
        </Text>
        <Button size="compact-xs" variant={open ? "filled" : "default"} onClick={() => setOpen(!open)} data-testid="prop-picture-replace">
          {open ? "Скрыть выбор" : "Заменить"}
        </Button>
        {source && (
          <SegmentedControl
            size="xs"
            value={fit}
            onChange={(v) => setPicture({ fit: v as "cover" | "contain" })}
            data={[
              { value: "cover", label: "Заполнить рамку" },
              { value: "contain", label: "Вписать" },
            ]}
            data-testid="prop-fit"
          />
        )}
      </Group>
      {open && (
        <AssetPicker {...picker} preferKind={isIcon ? "icon" : "photo"} onPick={onPick} onClose={() => setOpen(false)} />
      )}
      {canRecolor && (
        <Group gap="sm" align="flex-end" wrap="wrap">
          <ColorField label="Цвет иконки" value={op?.picture?.color} tokens={tokens} onChange={(hex) => setPicture({ color: hex })} testId="prop-icon-color" />
          <Text size="xs" c="dimmed" pb={6}>перекрашиваются только одноцветные иконки; остальные останутся как есть</Text>
        </Group>
      )}
    </Stack>
  );
}

// ---------- фон слайда ----------

function BackgroundProperties({ editor, tokens, picker }: { editor: SlideEditor; tokens: TemplateTokens; picker: PickerProps }) {
  const [open, setOpen] = useState(false);
  const op = findOverride(editor.draft, "background", null);
  const current = editor.previewSlide?.background ?? { kind: "inherited" };
  const kind = op?.background?.kind ?? (current.kind === "solid" ? "solid" : current.kind === "image" ? "image" : "inherited");
  const onPick = (src: PictureSource, url: string) => {
    if (src.kind === "file" && src.file_id) editor.registerFileUrl(src.file_id, url);
    editor.setOp({ op: "background", background: { kind: "image", source: src, fit: op?.background?.fit ?? "cover" } });
    setOpen(false);
  };
  return (
    <Stack gap="xs" data-testid="background-panel">
      <Text size="xs" fw={500}>Фон слайда</Text>
      <SegmentedControl
        size="xs"
        value={kind}
        onChange={(v) => {
          if (v === "inherited") editor.setOp({ op: "background", background: { kind: "inherited" } });
          else if (v === "solid") editor.setOp({ op: "background", background: { kind: "solid", color: op?.background?.color ?? current.color ?? tokens.colors[0]?.hex ?? "#FFFFFF" } });
          else setOpen(true);
        }}
        data={[
          { value: "inherited", label: "Как в макете" },
          { value: "solid", label: "Цвет" },
          { value: "image", label: "Картинка" },
        ]}
        data-testid="bg-kind"
      />
      {kind === "solid" && (
        <ColorField
          label="Цвет фона"
          value={op?.background?.color ?? current.color}
          tokens={tokens}
          onChange={(hex) => editor.setOp({ op: "background", background: { kind: "solid", color: hex } })}
          testId="bg-color"
        />
      )}
      {(kind === "image" || open) && (
        <Group gap="sm" align="center" wrap="wrap">
          <Text size="xs">Фон-картинка{op?.background?.source?.name ? ` (${op.background.source.name})` : ""}</Text>
          <Button size="compact-xs" variant={open ? "filled" : "default"} onClick={() => setOpen(!open)} data-testid="bg-picture-replace">{open ? "Скрыть выбор" : "Заменить"}</Button>
          {op?.background?.kind === "image" && (
            <SegmentedControl
              size="xs"
              value={op.background.fit ?? "cover"}
              onChange={(v) => editor.setOp({ op: "background", background: { ...op.background, kind: "image", fit: v as "cover" | "contain" } })}
              data={[
                { value: "cover", label: "Заполнить" },
                { value: "contain", label: "Вписать" },
              ]}
              data-testid="bg-fit"
            />
          )}
        </Group>
      )}
      {open && <AssetPicker {...picker} preferKind="photo" onPick={onPick} onClose={() => setOpen(false)} />}
    </Stack>
  );
}
