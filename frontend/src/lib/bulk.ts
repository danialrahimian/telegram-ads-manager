/** Helpers for selecting ads and showing bulk operations. */
import { num } from "@/lib/format";
import type { AdRow, BulkItemStatus, BulkKind, BulkMode } from "@/lib/types";

export function toggleOne(selected: ReadonlySet<number>, id: number): Set<number> {
  const next = new Set(selected);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  return next;
}

/** Header checkbox: select every visible id, or clear them all when they are already selected. */
export function toggleAll(selected: ReadonlySet<number>, ids: number[]): Set<number> {
  const next = new Set(selected);
  const all = ids.length > 0 && ids.every((i) => next.has(i));
  for (const i of ids) {
    if (all) next.delete(i);
    else next.add(i);
  }
  return next;
}

export function headerState(selected: ReadonlySet<number>, ids: number[]): boolean | "indeterminate" {
  const n = ids.filter((i) => selected.has(i)).length;
  if (n === 0) return false;
  return n === ids.length ? true : "indeterminate";
}

/** "بی‌بودجه از N روز پیش" for an ad without budget, or null. */
export function budgetless(ad: Pick<AdRow, "remaining" | "empty_days">): string | null {
  if (ad.remaining > 0.01) return null;
  if (ad.empty_days == null) return "بی‌بودجه";
  if (ad.empty_days === 0) return "بی‌بودجه از امروز";
  return `بی‌بودجه از ${num(ad.empty_days)} روز پیش`;
}

export const BULK_KIND_LABEL: Record<BulkKind, string> = {
  content: "عوض کردن متن/عکس",
  referral: "لینک رفرال اختصاصی",
  replace: "جایگزینی آگهی",
};

export const BULK_MODE_LABEL: Record<BulkMode, string> = {
  replace: "جایگزینی (آگهی تازه)",
  inplace: "ویرایش در جا",
};

export const ITEM_VARIANT: Record<BulkItemStatus, "success" | "warning" | "destructive" | "secondary" | "info"> = {
  pending: "secondary",
  submitted: "info",
  in_review: "info",
  approved: "success",
  declined: "destructive",
  retired: "success",
  failed: "destructive",
  skipped: "secondary",
};

export const STEP_LABEL: Record<string, string> = {
  pause: "توقف آگهی قدیمی",
  withdraw: "برداشت بودجه‌ی قدیمی (۱۰ دقیقه بعد از توقف)",
  transfer: "انتقال بودجه به آگهی جدید",
  transfer_wait: "انتقال بودجه به آگهی جدید",
  retire: "بایگانی/حذف آگهی قدیمی",
  delete_wait: "حذف آگهی قدیمی",
  done: "",
};

/** Same rule as the backend (services/placement.py): only channel ads carry a photo. */
export function supportsPhoto(t: { placement?: string; target_type?: string }): boolean {
  if (t.placement) return t.placement === "channel_post" || t.placement === "channels";
  return t.target_type === "channels";
}
