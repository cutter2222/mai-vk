"use client";

import { Alert } from "@mantine/core";
import { useSearchParams } from "next/navigation";
import { OfficeEditor } from "@/components/office/OfficeEditor";

export function OfficeClient() {
  const id = useSearchParams().get("id");
  if (!id) return <Alert color="red">Офисная копия не указана. Откройте её из проекта.</Alert>;
  return <OfficeEditor key={id} id={id} />;
}