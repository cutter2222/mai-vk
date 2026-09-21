"use client";

import { useSearchParams } from "next/navigation";

import { TemplateDetail } from "@/components/templates/TemplateDetail";
import { TemplateLibrary } from "@/components/templates/TemplateLibrary";

/** Без параметра — сетка библиотеки, с `?id=` — карточка шаблона со всем, что извлёк анализ. */
export function TemplatesClient() {
  const id = useSearchParams().get("id");
  return id ? <TemplateDetail key={id} templateId={id} /> : <TemplateLibrary />;
}
