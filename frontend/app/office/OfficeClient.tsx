"use client";

import { Alert } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useRouter, useSearchParams } from "next/navigation";
import { OfficeEditor } from "@/components/office/OfficeEditor";

export function OfficeClient() {
  const params = useSearchParams();
  const router = useRouter();
  const id = params.get("id");
  const project = params.get("project");
  if (!id) return <Alert color="red">Офисная копия не указана. Откройте её из проекта.</Alert>;
  const back = new URLSearchParams();
  if (project) back.set("id", project);
  for (const key of ["officeJob", "officeArtifact"]) {
    const value = params.get(key);
    if (value) back.set(key, value);
  }
  // Completing a fullscreen session must not immediately open another editing session.
  if (project) back.set("officeView", "preview");
  const returnHref = project ? `/project?${back}` : "/";
  return <OfficeEditor key={id} id={id} returnHref={returnHref} onSaved={project ? () => {
    router.replace(returnHref);
    notifications.show({ color: "green", message: "Сохранено", position: "top-right", autoClose: 4000 });
  } : undefined} />;
}