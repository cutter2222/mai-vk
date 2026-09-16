import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Статический экспорт: интерфейс отдаёт Caddy с того же origin, что и API (FRAMEWORKS.md §1).
  output: "export",
  images: { unoptimized: true },
  trailingSlash: false,
  // Только для next dev в рабочем режиме: запросы к /api уходят на локальный API. В экспорт не попадает.
  ...(process.env.NODE_ENV === "development"
    ? {
        async rewrites() {
          return [{ source: "/api/:path*", destination: `${process.env.API_PROXY ?? "http://localhost:8000"}/api/:path*` }];
        },
      }
    : {}),
};

export default nextConfig;
