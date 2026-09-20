"use client";

import { Group, Stack, Text, Title } from "@mantine/core";

import type { TemplateProfile } from "@/lib/api/types";

/**
 * Дизайн-код загруженного файла: палитра, шрифты, шкала кеглей, пластика и поля.
 *
 * Подан вертикально — заголовок раздела, под ним само содержимое. Плитки в ряд заставляли
 * читать по горизонтали и оставляли половину карточек пустыми, когда в шаблоне один шрифт.
 * Служебные подробности разбора (сколько раз встретился цвет, с какой уверенностью) сюда не
 * идут: им место в JSON профиля.
 */
export function DesignCodeSection({ profile }: { profile: TemplateProfile }) {
  const { colors, typography, spacing } = profile.design_tokens;
  const shape = (profile.design_tokens as { shape?: Record<string, number | string> }).shape;

  const palette = colors.palette.slice(0, 16);
  const fonts = typography.fonts.slice(0, 5);
  const scale = [...typography.scale]
    .sort((a, b) => b.size_pt - a.size_pt)
    .map((s) => s.size_pt)
    .filter((v, i, arr) => arr.indexOf(v) === i)
    .slice(0, 12);
  const margins = spacing?.margins;

  return (
    <Stack gap={36}>
      <section>
        <Title order={4} className="code-title">Палитра</Title>
        <div className="swatch-row">
          {palette.map((c) => (
            <div key={`${c.hex}-${c.role}`} className="swatch" title={`${c.hex} · ${c.role}`}>
              <span style={{ background: c.hex }} />
              <b>{c.hex}</b>
            </div>
          ))}
        </div>
      </section>

      <section>
        <Title order={4} className="code-title">Шрифты</Title>
        <Stack gap={18}>
          {fonts.map((f) => (
            <div key={f.family} className="font-row">
              <div className="font-row-meta">
                <Text size="sm" fw={600}>{f.family}</Text>
                <Text size="xs" c="dimmed">
                  {[...new Set(f.roles ?? [])].join(", ") || "текст"}
                  {f.fallback ? ` · показывается как ${f.fallback}` : ""}
                </Text>
              </div>
              {/* Демонстрация той же гарнитурой: видно, что за шрифт, а не только его имя. */}
              <Text className="font-row-sample" style={{ fontFamily: `"${f.family}", sans-serif` }}>
                Съешь же ещё этих мягких булок 0123
              </Text>
            </div>
          ))}
        </Stack>
      </section>

      <section>
        <Title order={4} className="code-title">Шкала кеглей</Title>
        <div className="scale-row">
          {scale.map((size) => (
            <span key={size} className="scale-step">
              <b style={{ fontSize: Math.max(12, Math.min(size, 40)) }}>Аа</b>
              <i>{size}</i>
            </span>
          ))}
        </div>
      </section>

      <section>
        <Title order={4} className="code-title">Пластика и поля</Title>
        <Group gap={40} align="center" wrap="wrap">
          <div className="plastic-sample">
            <span
              style={{
                borderRadius:
                  shape?.card_geometry === "roundRect"
                    ? `${Math.round(Number(shape?.corner_ratio ?? 0) * 100)}%`
                    : 2,
                border: `${Number(shape?.stroke_pt ?? 1) || 1}px solid var(--line)`,
                boxShadow: Number(shape?.shadow_share ?? 0) >= 0.4 ? "var(--shadow-soft)" : "none",
              }}
            />
            <Text size="xs" c="dimmed">
              {shape?.card_geometry === "roundRect" ? "скруглённые плашки" : "прямые углы"}
            </Text>
          </div>
          {margins ? (
            <div className="margins-sample">
              <span
                style={{
                  paddingTop: `${(margins.top ?? 0) * 100}%`,
                  paddingRight: `${(margins.right ?? 0) * 100}%`,
                  paddingBottom: `${(margins.bottom ?? 0) * 100}%`,
                  paddingLeft: `${(margins.left ?? 0) * 100}%`,
                }}
              >
                <i />
              </span>
              <Text size="xs" c="dimmed">
                поля{" "}
                {[margins.top, margins.right, margins.bottom, margins.left]
                  .map((v) => `${Math.round((v ?? 0) * 100)}%`)
                  .join(" · ")}
              </Text>
            </div>
          ) : null}
        </Group>
      </section>
    </Stack>
  );
}
