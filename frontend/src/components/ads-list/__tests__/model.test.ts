import {
  activeFilterCount,
  loadColumns,
  matches,
  parseFilters,
  RESET_PATCH,
  saveColumns,
  sortRows,
  statusKey,
  writeFilters,
} from "@/components/ads-list/model";
import { supportsPhoto } from "@/lib/bulk";
import { faDigits } from "@/lib/format";
import { enDate, enMoney, enNum, enPct } from "@/lib/format-en";
import type { AdRow } from "@/lib/types";

function ad(over: Partial<AdRow>): AdRow {
  return {
    ad_id: 1,
    title: "ad",
    text: "متن",
    target_label: "@chan",
    target_type: "channels",
    placement: "channel_post",
    status: "active",
    is_paused: false,
    cpm: 0.4,
    remaining: 1,
    tag_id: null,
    variant_id: null,
    has_photo: false,
    created_date: Date.UTC(2026, 9, 1) / 1000,
    views: 1000,
    clicks: 30,
    starts: 10,
    spent: 0.5,
    empty_days: null,
    buyers: null,
    cpp: null,
    tag_name: null,
    ...over,
  } as AdRow;
}

describe("ads list model", () => {
  it("uses Telegram Ads status words", () => {
    expect(statusKey({ status: "active", is_paused: false })).toBe("active");
    expect(statusKey({ status: "active", is_paused: true })).toBe("on_hold");
    expect(statusKey({ status: "ready_for_review", is_paused: false })).toBe("in_review");
    expect(statusKey({ status: "declined", is_paused: false })).toBe("declined");
    expect(statusKey({ status: "stopped", is_paused: true })).toBe("stopped");
  });

  it("round-trips filters through the query string and keeps other keys", () => {
    const base = new URLSearchParams("select=all&bulk=referral");
    const q = writeFilters(base, { status: ["active", "in_review"], period: "today", referral: "without", photo: true });
    expect(q.get("status")).toBe("active,in_review");
    expect(q.get("period")).toBe("today");
    expect(q.get("select")).toBe("all");
    const f = parseFilters(q);
    expect(f.status).toEqual(["active", "in_review"]);
    expect(f.referral).toBe("without");
    expect(f.photo).toBe(true);
    expect(activeFilterCount(f)).toBe(3);
    const reset = parseFilters(writeFilters(q, RESET_PATCH));
    expect(activeFilterCount(reset)).toBe(0);
    expect(reset.period).toBe("7d");
    expect(parseFilters(new URLSearchParams("period=bogus&status=nope")).status).toEqual([]);
  });

  it("combines every filter", () => {
    const rows = [
      ad({ ad_id: 1 }),
      ad({ ad_id: 2, status: "declined" }),
      ad({ ad_id: 3, tag_id: 5, tag_name: "x" }),
      ad({ ad_id: 4, placement: "bot_banner", target_type: "bots", remaining: 0 }),
      ad({ ad_id: 5, cpm: 2, has_photo: true }),
    ];
    const f = parseFilters(new URLSearchParams("status=active&referral=without"));
    expect(rows.filter((a) => matches(a, f)).map((a) => a.ad_id)).toEqual([1, 4, 5]);
    expect(rows.filter((a) => matches(a, parseFilters(new URLSearchParams("placement=bots")))).map((a) => a.ad_id)).toEqual([4]);
    expect(rows.filter((a) => matches(a, parseFilters(new URLSearchParams("budget=out")))).map((a) => a.ad_id)).toEqual([4]);
    expect(rows.filter((a) => matches(a, parseFilters(new URLSearchParams("cpm_min=1&photo=1")))).map((a) => a.ad_id)).toEqual([5]);
    expect(rows.filter((a) => matches(a, parseFilters(new URLSearchParams("q=4")))).map((a) => a.ad_id)).toEqual([4]);
    expect(rows.filter((a) => matches(a, parseFilters(new URLSearchParams("added_from=2026-10-02")))).length).toBe(0);
  });

  it("sorts by the period's numbers, empty values last", () => {
    const rows = [
      ad({ ad_id: 1, period_stats: { views: 10, clicks: 1, actions: 1, spent: 0.1 } }),
      ad({ ad_id: 2, period_stats: { views: 10, clicks: 1, actions: 0, spent: 0.3 } }),
      ad({ ad_id: 3, period_stats: { views: 10, clicks: 1, actions: 2, spent: 0.12 } }),
    ];
    expect(sortRows(rows, "spent", "desc").map((a) => a.ad_id)).toEqual([2, 3, 1]);
    expect(sortRows(rows, "cpa", "asc").map((a) => a.ad_id)).toEqual([3, 1, 2]); // ad 2 has no actions: CPA empty
    expect(sortRows(rows, "cpa", "desc").map((a) => a.ad_id)).toEqual([1, 3, 2]);
  });

  it("remembers columns, and survives broken storage", () => {
    saveColumns(["views", "spent"]);
    expect(loadColumns()).toEqual(["views", "spent"]);
    localStorage.setItem("tgads.adsColumns.v1", "{not json");
    expect(loadColumns()).toContain("cpm");
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(loadColumns()).toContain("status");
    spy.mockRestore();
  });

  it("formats in English", () => {
    expect(enNum(12345)).toBe("12,345");
    expect(enMoney(0.12345, "TON")).toBe("0.1235 TON");
    expect(enPct(0.0312)).toBe("3.12%");
    expect(enDate(Date.UTC(2026, 9, 26) / 1000)).toBe("Oct 26, 2026");
  });

  it("photo only for channel placements", () => {
    expect(supportsPhoto({ placement: "channel_post" })).toBe(true);
    expect(supportsPhoto({ placement: "bot_banner", target_type: "bots" })).toBe(false);
    expect(supportsPhoto({ target_type: "channels" })).toBe(true);
  });
});

describe("faDigits", () => {
  it("turns a server sentence's digits Persian", () => {
    expect(faDigits("برای ساخت 3 آگهی جدید، 3 × 1 = 3 TON لازم است؛ موجودی فعلی 1.5 TON است.")).toBe(
      "برای ساخت ۳ آگهی جدید، ۳ × ۱ = ۳ TON لازم است؛ موجودی فعلی ۱٫۵ TON است.",
    );
  });
});
