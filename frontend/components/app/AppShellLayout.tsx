"use client";

import { AppShell, Badge, Container, Group, Text, Tooltip } from "@mantine/core";
import { IconPresentation } from "@tabler/icons-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { HealthIndicator } from "./HealthIndicator";

const NAV = [
  { href: "/", label: "Новая презентация" },
  { href: "/templates", label: "Шаблоны" },
];

export function AppShellLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <AppShell header={{ height: 56 }} padding="lg" withBorder>
      <AppShell.Header style={{ background: "var(--mantine-color-body)" }}>
        <Container size="xl" h="100%">
          <Group h="100%" justify="space-between">
            <Group gap="lg">
              <Link href="/" style={{ textDecoration: "none", color: "inherit" }}>
                <Group gap="xs">
                  <IconPresentation size={22} stroke={1.6} />
                  <Text fw={600}>Цифровой дизайнер презентаций</Text>
                </Group>
              </Link>
              <Group gap="md">
                {NAV.map((n) => (
                  <Text key={n.href} component={Link} href={n.href} size="sm" c={pathname === n.href ? "blue" : "dimmed"} fw={pathname === n.href ? 600 : 400} style={{ textDecoration: "none" }}>
                    {n.label}
                  </Text>
                ))}
              </Group>
            </Group>
            <Group gap="sm">
              <Tooltip label="Интерфейс работает на данных-заглушках; слои генерации подключаются по этапам" withArrow>
                <Badge variant="light" color="gray" data-testid="mode-badge">
                  {process.env.NEXT_PUBLIC_API_MODE === "mock" ? "Режим заглушек" : "Рабочий режим"}
                </Badge>
              </Tooltip>
              <HealthIndicator />
            </Group>
          </Group>
        </Container>
      </AppShell.Header>
      <AppShell.Main>
        <Container size="xl">{children}</Container>
      </AppShell.Main>
    </AppShell>
  );
}
