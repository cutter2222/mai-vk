import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Статический экспорт: интерфейс отдаётся FastAPI с того же origin (FRAMEWORKS.md §1).
  output: "export",
  images: { unoptimized: true },
  trailingSlash: false,
};

export default nextConfig;
