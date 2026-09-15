/** Режим API фиксируется при сборке: публичные переменные Next.js встраиваются в bundle. */
export const API_MODE: "mock" | "real" = process.env.NEXT_PUBLIC_API_MODE === "mock" ? "mock" : "real";

/** Базовый путь API. На сервере интерфейс отдаётся с того же origin, поэтому путь относительный. */
export const API_BASE: string = process.env.NEXT_PUBLIC_API_BASE ?? "/api";
