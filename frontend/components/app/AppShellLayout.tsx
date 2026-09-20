"use client";

import { AppShell, Container, Group, Text, UnstyledButton } from "@mantine/core";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";

import { HealthIndicator } from "./HealthIndicator";
import { Logo } from "./Logo";

const NAV = [
  { href: "/", label: "Презентации", match: (p: string) => p === "/" || p.startsWith("/project") },
  { href: "/templates", label: "Шаблоны", match: (p: string) => p.startsWith("/templates") },
];

/**
 * Шапка приложения: знак, два раздела, состояние сервиса. На экране проекта не рисуется —
 * там своя шапка, и две строки подряд были бы лишним слоем.
 *
 * Витрина дизайн-системы интерфейса живёт по адресу /design и в навигацию не вынесена:
 * это служебная страница для разработки, а не раздел продукта.
 *
 * По правилу «no clutter in the chrome» здесь нет ничего, на что нельзя нажать.
 * Режим API больше не висит отдельной плашкой: в рабочем режиме она сообщала то, что и так
 * норма, а в режиме заглушек об этом говорит индикатор состояния.
 */
export function AppShellLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() ?? "/";
  const openedId = useSearchParams()?.get("id");
  // Внутри открытой сущности разделов нет: человек работает с презентацией или разбирает
  // шаблон, а не ходит по библиотеке. У обоих экранов своя шапка с возвратом и действиями,
  // и вторая строка над ней была бы лишним слоем.
  const bare = pathname.startsWith("/project") || (pathname.startsWith("/templates") && !!openedId);
  if (bare) {
    return (
      <AppShell padding={0}>
        <AppShell.Main>{children}</AppShell.Main>
      </AppShell>
    );
  }
  return (
    <AppShell header={{ height: 60 }} padding={0}>
      <AppShell.Header
        style={{ background: "var(--page)", borderBottom: "1px solid var(--line)" }}
      >
        {/* Шапка живёт в той же сетке, что и содержимое страниц: знак слева стоит ровно под
            заголовком экрана, а состояние справа — под правым краем сетки карточек. Раньше
            у шапки были свои отступы, и она не совпадала с контентом. */}
        <Container size="xl" h="100%" px="md">
          <Group h="100%" justify="space-between" wrap="nowrap">
          <Group gap={24} wrap="nowrap">
            {/* Знак без подписи: название сервиса не соревнуется с заголовком экрана. */}
            <Link
              href="/"
              aria-label="Дизайнер презентаций — к списку"
              style={{ display: "inline-flex", textDecoration: "none" }}
            >
              <Logo />
            </Link>
            <Group gap={4}>
              {NAV.map((n) => {
                const active = n.match(pathname);
                return (
                  <UnstyledButton
                    key={n.href}
                    component={Link}
                    href={n.href}
                    px={12}
                    py={7}
                    className="nav-link"
                    data-active={active || undefined}
                    data-testid={`nav-${n.href === "/" ? "home" : n.href.slice(1)}`}
                  >
                    <Text size="sm" fw={active ? 600 : 500} inherit>
                      {n.label}
                    </Text>
                  </UnstyledButton>
                );
              })}
            </Group>
          </Group>
            <HealthIndicator />
          </Group>
        </Container>
      </AppShell.Header>
      <AppShell.Main>{children}</AppShell.Main>
    </AppShell>
  );
}
