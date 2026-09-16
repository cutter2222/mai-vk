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
    let alive = true;
    void (async () => {
      const found = await findProjectByJob(jobId);
      const id = found?.project_id ?? (await createProject({ title: `Задание ${jobId}`, job_id: jobId })).project_id;
      if (alive) router.replace(`/project?id=${encodeURIComponent(id)}`);
    })();
    return () => {
      alive = false;
    };
  }, [jobId, router]);

  return null;
}
