import { Badge } from "@/components/ui/badge";

const MAP: Record<string, { label: string; variant: "success" | "warning" | "destructive" | "secondary" | "info" }> = {
  active: { label: "در حال اجرا", variant: "success" },
  in_review: { label: "در بررسی", variant: "info" },
  ready_for_review: { label: "ارسال‌نشده برای بررسی", variant: "warning" },
  declined: { label: "رد شده", variant: "destructive" },
  stopped: { label: "متوقف", variant: "secondary" },
  on_hold: { label: "معلق", variant: "warning" },
};

export function AdStatusBadge({ status, paused }: { status: string; paused?: boolean }) {
  if (paused && status === "active") return <Badge variant="warning">مکث</Badge>;
  const m = MAP[status] ?? { label: status || "نامشخص", variant: "secondary" as const };
  return <Badge variant={m.variant}>{m.label}</Badge>;
}

export function placementLabel(p: string): string {
  if (p === "bot_banner" || p === "bots") return "ربات";
  if (p === "channel_post" || p === "channels") return "کانال";
  if (p === "search") return "جستجو";
  return p || "—";
}
