import { Badge } from "@mantine/core";

import { STATUS_LABELS } from "@/lib/format";

const COLORS: Record<string, string> = {
  queued: "gray",
  pending: "gray",
  running: "blue",
  succeeded: "green",
  ready: "green",
  complete: "green",
  needs_review: "green",
  partial: "yellow",
  failed: "red",
  canceled: "gray",
  skipped: "gray",
};

export function StatusBadge({ status, size = "sm" }: { status: string; size?: string }) {
  return (
    <Badge color={COLORS[status] ?? "gray"} variant="light" size={size} data-testid={`status-${status}`}>
      {STATUS_LABELS[status] ?? status}
    </Badge>
  );
}
