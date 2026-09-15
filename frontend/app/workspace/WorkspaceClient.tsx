"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect } from "react";

import { createProject, findProjectByJob } from "@/lib/state/projects";

/** Прежний адрес /workspace?job=… ведёт в проект с этим заданием; для незнакомого задания проект создаётся. */
export function WorkspaceClient() {
  const params = useSearchParams();
  const router = useRouter();
  const jobId = params.get("job");

  useEffect(() => {
    if (!jobId) {
      router.replace("/");
      return;
    }
    const project = findProjectByJob(jobId) ?? createProject({ title: `Задание ${jobId}`, job_id: jobId });
    router.replace(`/project?id=${encodeURIComponent(project.id)}`);
  }, [jobId, router]);

  return null;
}
