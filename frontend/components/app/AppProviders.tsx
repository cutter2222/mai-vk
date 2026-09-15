"use client";

import { MantineProvider } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { useEffect, useState } from "react";

import { API_MODE } from "@/lib/api/config";
import { theme } from "@/lib/theme";

/**
 * Провайдеры Mantine. В сборке mock перед первым запросом поднимается worker MSW;
 * в сборке real заглушки не подключаются вовсе (код мока не попадает в bundle).
 */
export function AppProviders({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(API_MODE !== "mock");

  useEffect(() => {
    if (API_MODE !== "mock") return;
    let cancelled = false;
    import("@/mocks/browser")
      .then(({ startMocks }) => startMocks())
      .then(() => {
        if (!cancelled) setReady(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <MantineProvider theme={theme} defaultColorScheme="light">
      <Notifications position="top-right" limit={4} />
      {ready ? children : null}
    </MantineProvider>
  );
}
