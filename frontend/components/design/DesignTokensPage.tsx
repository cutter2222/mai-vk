"use client";

import { Badge, Button, Card, Container, Group, Stack, Text, TextInput, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";

/**
 * Витрина дизайн-системы: всё, из чего собираются экраны, в одном месте.
 *
 * Каждый образец копируется кликом, и в буфер уходит `var(--токен)`, а не значение:
 * литерал цвета в разметке — ошибка, новый цвет заводится в `app/globals.css` и
 * `lib/theme.ts` одновременно.
 */

type Token = { name: string; value: string; note: string };

const BRAND: Token[] = [
  { name: "--accent", value: "#1F6FEB", note: "главное действие, выбор, ссылки" },
  { name: "--accent-dark", value: "#1A5CC4", note: "нажатие и наведение" },
  { name: "--accent-soft", value: "#D6E4FF", note: "подложка выбранного" },
];

const SURFACES: Token[] = [
  { name: "--page", value: "#FFFFFF", note: "холст страницы" },
  { name: "--card", value: "#F7F8FA", note: "панель, вторая поверхность" },
  { name: "--sky", value: "#EEF4FF", note: "холодная подложка акцента" },
  { name: "--mist", value: "#F1F2F5", note: "нейтральная подложка" },
  { name: "--line", value: "#ECEEF2", note: "рамки и разделители" },
];

const TEXT: Token[] = [
  { name: "--ink", value: "#1A1A1A", note: "основной текст" },
  { name: "--ink2", value: "#6B7280", note: "подписи и метаданные" },
];

const STATE: Token[] = [
  { name: "--success", value: "#2E7D4F", note: "готово — только текст и иконка" },
  { name: "--danger", value: "#C0392B", note: "ошибка — только текст и иконка" },
];

const RADII = [
  { name: "xs", value: "8px" },
  { name: "sm", value: "12px" },
  { name: "md", value: "16px" },
  { name: "lg", value: "24px" },
  { name: "xl", value: "32px" },
];

const SHADOWS = [
  { name: "--shadow-flat", note: "лёгкое отделение" },
  { name: "--shadow-soft", note: "карточка под курсором" },
  { name: "--shadow-lift", note: "всплывающий слой" },
  { name: "--shadow-ring", note: "рамка вместо тени на цветной подложке" },
];

const TYPE = [
  { sample: "Презентации", cls: "h1", spec: "34 / 1.08 / 700", use: "заголовок экрана" },
  { sample: "Разбор шаблона", cls: "h2", spec: "24 / 1.15 / 700", use: "раздел" },
  { sample: "Карточки · 3", cls: "h3", spec: "18 / 1.25 / 600", use: "заголовок блока" },
  { sample: "Обычный текст интерфейса", cls: "md", spec: "15 / 1.55", use: "основной" },
  { sample: "Подпись под карточкой", cls: "sm", spec: "13 / 1.45", use: "метаданные" },
  { sample: "ВСПОМОГАТЕЛЬНОЕ", cls: "xs", spec: "12 / 1.4", use: "чипы и служебное" },
];

const RULES = [
  "Поверхности больше 64 px — только нейтральные или холодные. Акцент крупной заливкой не кладётся.",
  "Цвета состояний — текст и иконки, никогда не заливка.",
  "Тени только из холодной семьи. На цветной подложке тень грязнит — там волосяная рамка.",
  "Одно главное действие на экран. Второе по важности — нейтральной кнопкой.",
  "Хардкод цвета в разметке запрещён: новый цвет заводится в globals.css и theme.ts.",
  "Когда сомневаешься — убери элемент, а не добавь.",
];

function copy(token: string) {
  const text = `var(${token})`;
  void navigator.clipboard?.writeText(text);
  notifications.show({ message: `Скопировано: ${text}`, color: "brand", autoClose: 1600 });
}

function Swatch({ token }: { token: Token }) {
  return (
    <button type="button" className="token-swatch" onClick={() => copy(token.name)} title={`Копировать var(${token.name})`}>
      <span className="token-swatch-chip" style={{ background: `var(${token.name})` }} />
      <span className="token-swatch-body">
        <b>{token.name}</b>
        <i>{token.value}</i>
        <em>{token.note}</em>
      </span>
    </button>
  );
}

function Block({ title, children, aside }: { title: string; children: React.ReactNode; aside?: string }) {
  return (
    <section>
      <Group justify="space-between" align="baseline" mb="sm">
        <Title order={3}>{title}</Title>
        {aside ? <Text size="xs" c="dimmed">{aside}</Text> : null}
      </Group>
      {children}
    </section>
  );
}

export function DesignTokensPage() {
  return (
    <div className="page-surface">
      <Container size="xl" py="xl">
        <Stack gap={44}>
          <div>
            <Title order={1} style={{ letterSpacing: "-0.03em" }}>Дизайн-система</Title>
            <Text c="dimmed" size="sm" mt={6} maw={680}>
              Откуда берутся цвета, размеры и компоненты интерфейса. Клик по образцу копирует имя
              токена — вставляйте его как <code>var(--токен)</code>, а не значение.
            </Text>
          </div>

          <Block title="Бренд" aside="акцент — один на всё приложение">
            <div className="token-grid">{BRAND.map((t) => <Swatch key={t.name} token={t} />)}</div>
          </Block>

          <Block title="Поверхности" aside="крупные блоки — только отсюда">
            <div className="token-grid">{SURFACES.map((t) => <Swatch key={t.name} token={t} />)}</div>
          </Block>

          <Block title="Текст и состояния">
            <div className="token-grid">{[...TEXT, ...STATE].map((t) => <Swatch key={t.name} token={t} />)}</div>
          </Block>

          <Block title="Типографика" aside="Golos Text">
            <Stack gap={0} className="type-table">
              {TYPE.map((t) => (
                <Group key={t.cls} justify="space-between" align="center" wrap="nowrap" py={12}>
                  <Text className={`type-sample type-${t.cls}`} lineClamp={1}>{t.sample}</Text>
                  <Group gap="lg" wrap="nowrap">
                    <Text size="xs" c="dimmed" ff="monospace">{t.spec}</Text>
                    <Text size="xs" c="dimmed" w={150} ta="right">{t.use}</Text>
                  </Group>
                </Group>
              ))}
            </Stack>
          </Block>

          <Block title="Скругления и тени">
            <Group gap="xl" align="flex-start">
              <div className="radius-row">
                {RADII.map((r) => (
                  <div key={r.name} className="radius-sample">
                    <span style={{ borderRadius: r.value }} />
                    <Text size="xs" c="dimmed">{r.name} · {r.value}</Text>
                  </div>
                ))}
              </div>
              <div className="shadow-row">
                {SHADOWS.map((s) => (
                  <button key={s.name} type="button" className="shadow-sample" onClick={() => copy(s.name)} title={`Копировать var(${s.name})`}>
                    <span style={{ boxShadow: `var(${s.name})` }} />
                    <Text size="xs" c="dimmed">{s.name.replace("--shadow-", "")}</Text>
                    <Text size="xs" c="dimmed">{s.note}</Text>
                  </button>
                ))}
              </div>
            </Group>
          </Block>

          <Block title="Компоненты" aside="Mantine с настройками из lib/theme.ts">
            <Group gap="md" align="flex-start" wrap="wrap">
              <Card w={280}>
                <Stack gap="sm">
                  <Text fw={600}>Карточка</Text>
                  <Text size="sm" c="dimmed">Радиус lg, рамка по <code>--line</code>, тень только под курсором.</Text>
                  <Group gap="xs">
                    <Badge>Готов</Badge>
                    <Badge color="ink">Черновик</Badge>
                  </Group>
                </Stack>
              </Card>
              <Card w={280}>
                <Stack gap="sm">
                  <Text fw={600}>Действия</Text>
                  <Group gap="xs">
                    <Button>Главное</Button>
                    <Button variant="default">Второе</Button>
                  </Group>
                  <Button variant="subtle" size="compact-sm">Третье</Button>
                </Stack>
              </Card>
              <Card w={280}>
                <Stack gap="sm">
                  <Text fw={600}>Поле</Text>
                  <TextInput placeholder="Название презентации" />
                  <Text size="xs" c="dimmed">Фокус — рамкой акцента, без свечения.</Text>
                </Stack>
              </Card>
            </Group>
          </Block>

          <Block title="Правила" aside="обязательны">
            <Stack gap={8} component="ol" className="rule-list">
              {RULES.map((r) => <Text key={r} size="sm" component="li">{r}</Text>)}
            </Stack>
          </Block>
        </Stack>
      </Container>
    </div>
  );
}
