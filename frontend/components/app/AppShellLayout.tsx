"use client";

import { AppShell, Group, Text, Tooltip, UnstyledButton } from "@mantine/core";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { HealthIndicator } from "./HealthIndicator";

const NAV = [
  { href: "/", label: "Презентации", match: (p: string) => p === "/" || p.startsWith("/project") },
  { href: "/templates", label: "Шаблоны", match: (p: string) => p.startsWith("/templates") },
];

export function AppShellLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() ?? "/";
  const mock = process.env.NEXT_PUBLIC_API_MODE === "mock";
  return (
    <AppShell header={{ height: 56 }} padding={0} withBorder>
      <AppShell.Header style={{ background: "var(--mantine-color-body)", borderColor: "var(--mantine-color-gray-2)" }}>
        <Group h="100%" px="lg" justify="space-between" wrap="nowrap">
          <Group gap={28} wrap="nowrap">
            <Link href="/" style={{ textDecoration: "none", color: "inherit" }}>
              <Group gap={8} wrap="nowrap">
                <span aria-hidden style={{ display: "inline-block", width: 18, height: 12, borderRadius: 3, background: "var(--mantine-color-graphite-8)" }} />
                <Text fw={600} style={{ whiteSpace: "nowrap", letterSpacing: "-0.01em" }}>Дизайнер презентаций</Text>
              </Group>
            </Link>
            <Group gap={2}>
              {NAV.map((n) => {
                const active = n.match(pathname);
                return (
                  <UnstyledButton
                    key={n.href}
                    component={Link}
                    href={n.href}
                    px="sm"
                    py={6}
                    style={{ borderRadius: "var(--mantine-radius-sm)", background: active ? "var(--mantine-color-gray-1)" : undefined }}
                    data-testid={`nav-${n.href === "/" ? "home" : "templates"}`}
                  >
                    <Text size="sm" fw={500} c={active ? undefined : "dimmed"}>{n.label}</Text>
                  </UnstyledButton>
                );
              })}
            </Group>
          </Group>
          <Group gap="lg" wrap="nowrap">
            <Tooltip label={mock ? "Интерфейс работает на данных-заглушках; слои генерации подключаются по этапам" : "Интерфейс обращается к настоящему API"}>
              <span className="quiet-status" data-tone={mock ? "warn" : "ok"} data-testid="mode-badge">
                <i />
                {mock ? "Режим заглушек" : "Рабочий режим"}
              </span>
            </Tooltip>
            <HealthIndicator />
          </Group>
        </Group>
      </AppShell.Header>
      <AppShell.Main>{children}</AppShell.Main>
    </AppShell>
  );
}
