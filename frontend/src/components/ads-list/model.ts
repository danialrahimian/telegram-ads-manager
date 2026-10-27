/** Filters, statuses, columns and sorting of the Telegram-Ads-style ads list. Pure functions (tested). */
import { ratio } from "@/lib/format-en";
import type { AdRow } from "@/lib/types";

export type StatusKey = "active" | "on_hold" | "in_review" | "ready" | "declined" | "stopped";
export const STATUS_LABEL: Record<StatusKey, string> = {
  active: "Active",
  on_hold: "On Hold",
  in_review: "In Review",
  ready: "Ready for Review",
  declined: "Declined",
  stopped: "Stopped",
};
export const STATUS_KEYS = Object.keys(STATUS_LABEL) as StatusKey[];

/** Telegram Ads wording: a paused active ad is "On Hold". */
export function statusKey(a: Pick<AdRow, "status" | "is_paused">): StatusKey {
  if (a.status === "declined") return "declined";
  if (a.status === "in_review") return "in_review";
  // Saved on Telegram but not sent to review (an ad without budget is not sent by itself).
  if (a.status === "ready_for_review") return "ready";
  if (a.status === "stopped") return "stopped";
  if (a.status === "on_hold" || (a.status === "active" && a.is_paused)) return "on_hold";
  return a.status === "active" ? "active" : "stopped";
}

export type Placement = "channels" | "bots" | "search";
export function placementKey(a: Pick<AdRow, "placement" | "target_type">): Placement {
  if (a.placement === "bot_banner" || a.target_type === "bots") return "bots";
  if (a.placement === "search" || a.target_type === "search") return "search";
  return "channels";
}

export type PeriodKey = "today" | "yesterday" | "7d" | "30d" | "month" | "all" | "custom";
export const PERIOD_LABEL: Record<PeriodKey, string> = {
  today: "Today",
  yesterday: "Yesterday",
  "7d": "Last 7 days",
  "30d": "Last 30 days",
  month: "This month",
  all: "All time",
  custom: "Custom range",
};

export interface Filters {
  q: string;
  status: StatusKey[];
  placement: Placement | "";
  period: PeriodKey;
  from: string;
  to: string;
  cpmMin: string;
  cpmMax: string;
  budgetMin: string;
  budgetMax: string;
  budget: "" | "has" | "out";
  target: "" | "channel" | "bot";
  referral: "" | "with" | "without";
  variant: string;
  photo: boolean;
  addedFrom: string;
  addedTo: string;
  archived: boolean;
  sort: string;
  dir: "asc" | "desc";
}

const DEFAULTS: Filters = {
  q: "",
  status: [],
  placement: "",
  period: "7d",
  from: "",
  to: "",
  cpmMin: "",
  cpmMax: "",
  budgetMin: "",
  budgetMax: "",
  budget: "",
  target: "",
  referral: "",
  variant: "",
  photo: false,
  addedFrom: "",
  addedTo: "",
  archived: false,
  sort: "spent",
  dir: "desc",
};

const PARAM: Record<keyof Filters, string> = {
  q: "q",
  status: "status",
  placement: "placement",
  period: "period",
  from: "from",
  to: "to",
  cpmMin: "cpm_min",
  cpmMax: "cpm_max",
  budgetMin: "budget_min",
  budgetMax: "budget_max",
  budget: "budget",
  target: "target",
  referral: "referral",
  variant: "variant",
  photo: "photo",
  addedFrom: "added_from",
  addedTo: "added_to",
  archived: "archived",
  sort: "sort",
  dir: "dir",
};

export function parseFilters(p: URLSearchParams): Filters {
  const f: Filters = { ...DEFAULTS, status: [] };
  const get = (k: keyof Filters) => p.get(PARAM[k]) ?? "";
  f.q = get("q");
  f.status = get("status")
    .split(",")
    .filter((x): x is StatusKey => (STATUS_KEYS as string[]).includes(x));
  const pl = get("placement");
  f.placement = pl === "channels" || pl === "bots" || pl === "search" ? pl : "";
  const per = get("period") as PeriodKey;
  f.period = per in PERIOD_LABEL ? per : "7d";
  f.from = get("from");
  f.to = get("to");
  f.cpmMin = get("cpmMin");
  f.cpmMax = get("cpmMax");
  f.budgetMin = get("budgetMin");
  f.budgetMax = get("budgetMax");
  const b = get("budget");
  f.budget = b === "has" || b === "out" ? b : "";
  const t = get("target");
  f.target = t === "channel" || t === "bot" ? t : "";
  const r = get("referral");
  f.referral = r === "with" || r === "without" ? r : "";
  f.variant = get("variant");
  f.photo = get("photo") === "1";
  f.addedFrom = get("addedFrom");
  f.addedTo = get("addedTo");
  f.archived = get("archived") === "1";
  f.sort = get("sort") || DEFAULTS.sort;
  f.dir = get("dir") === "asc" ? "asc" : get("dir") === "desc" ? "desc" : DEFAULTS.dir;
  return f;
}

/** Changes to the URL query; keys not part of the filters (select, bulk) are kept. */
export function writeFilters(base: URLSearchParams, patch: Partial<Filters>): URLSearchParams {
  const next = new URLSearchParams(base);
  for (const [k, v] of Object.entries(patch) as [keyof Filters, Filters[keyof Filters]][]) {
    const name = PARAM[k];
    const value = Array.isArray(v) ? v.join(",") : typeof v === "boolean" ? (v ? "1" : "") : String(v ?? "");
    if (!value || value === String(DEFAULTS[k])) next.delete(name);
    else next.set(name, value);
  }
  return next;
}

export function activeFilterCount(f: Filters): number {
  const keys: (keyof Filters)[] = [
    "q",
    "placement",
    "cpmMin",
    "cpmMax",
    "budgetMin",
    "budgetMax",
    "budget",
    "target",
    "referral",
    "variant",
    "addedFrom",
    "addedTo",
  ];
  return keys.filter((k) => f[k]).length + (f.status.length ? 1 : 0) + (f.photo ? 1 : 0) + (f.archived ? 1 : 0);
}

export const RESET_PATCH: Partial<Filters> = Object.fromEntries(
  (Object.keys(DEFAULTS) as (keyof Filters)[]).filter((k) => k !== "sort" && k !== "dir").map((k) => [k, DEFAULTS[k]]),
) as Partial<Filters>;

function num(v: string): number | null {
  if (v.trim() === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function day(ts: number): string {
  return ts ? new Date(ts * 1000).toISOString().slice(0, 10) : "";
}

export function matches(a: AdRow, f: Filters, variantText?: string): boolean {
  const term = f.q.trim().toLowerCase();
  if (
    term &&
    !(
      String(a.ad_id).includes(term) ||
      a.title.toLowerCase().includes(term) ||
      a.text.toLowerCase().includes(term) ||
      a.target_label.toLowerCase().includes(term)
    )
  )
    return false;
  if (f.status.length && !f.status.includes(statusKey(a))) return false;
  if (f.placement && placementKey(a) !== f.placement) return false;
  const cmin = num(f.cpmMin);
  const cmax = num(f.cpmMax);
  if (cmin !== null && a.cpm < cmin) return false;
  if (cmax !== null && a.cpm > cmax) return false;
  const bmin = num(f.budgetMin);
  const bmax = num(f.budgetMax);
  if (bmin !== null && a.remaining < bmin) return false;
  if (bmax !== null && a.remaining > bmax) return false;
  if (f.budget === "has" && !(a.remaining > 0.01)) return false;
  if (f.budget === "out" && a.remaining > 0.01) return false;
  if (f.target === "channel" && a.target_type !== "channels") return false;
  if (f.target === "bot" && a.target_type !== "bots") return false;
  if (f.referral === "with" && !a.tag_id) return false;
  if (f.referral === "without" && a.tag_id) return false;
  if (f.variant && String(a.variant_id) !== f.variant && !(variantText && a.text.trim() === variantText.trim()))
    return false;
  if (f.photo && !a.has_photo) return false;
  if (f.addedFrom && day(a.created_date) < f.addedFrom) return false;
  if (f.addedTo && day(a.created_date) > f.addedTo) return false;
  return true;
}

// ------------------------------------------------------------------ columns

export interface PeriodNumbers {
  views: number;
  clicks: number;
  actions: number;
  spent: number;
}

export function periodNumbers(a: AdRow): PeriodNumbers {
  return a.period_stats ?? { views: a.views, clicks: a.clicks, actions: a.starts, spent: a.spent };
}

export type ColumnId =
  | "status"
  | "cpm"
  | "views"
  | "clicks"
  | "actions"
  | "ctr"
  | "cvr"
  | "cpc"
  | "cpa"
  | "spent"
  | "budget"
  | "target"
  | "added"
  | "buyers"
  | "cpp"
  | "referral"
  | "empty";

export interface ColumnDef {
  id: ColumnId;
  label: string;
  numeric: boolean;
  defaultVisible: boolean;
  extra?: boolean; // this panel's own columns (not in Telegram Ads)
  value: (a: AdRow) => number | string | null;
}

export const COLUMNS: ColumnDef[] = [
  { id: "status", label: "Status", numeric: false, defaultVisible: true, value: (a) => STATUS_KEYS.indexOf(statusKey(a)) },
  { id: "cpm", label: "CPM", numeric: true, defaultVisible: true, value: (a) => a.cpm },
  { id: "views", label: "Views", numeric: true, defaultVisible: true, value: (a) => periodNumbers(a).views },
  { id: "clicks", label: "Clicks", numeric: true, defaultVisible: true, value: (a) => periodNumbers(a).clicks },
  { id: "actions", label: "Actions", numeric: true, defaultVisible: true, value: (a) => periodNumbers(a).actions },
  {
    id: "ctr",
    label: "CTR",
    numeric: true,
    defaultVisible: true,
    value: (a) => ratio(periodNumbers(a).clicks, periodNumbers(a).views),
  },
  {
    id: "cvr",
    label: "CVR",
    numeric: true,
    defaultVisible: true,
    value: (a) => ratio(periodNumbers(a).actions, periodNumbers(a).clicks),
  },
  {
    id: "cpc",
    label: "CPC",
    numeric: true,
    defaultVisible: true,
    value: (a) => ratio(periodNumbers(a).spent, periodNumbers(a).clicks),
  },
  {
    id: "cpa",
    label: "CPA",
    numeric: true,
    defaultVisible: true,
    value: (a) => ratio(periodNumbers(a).spent, periodNumbers(a).actions),
  },
  { id: "spent", label: "Spent", numeric: true, defaultVisible: true, value: (a) => periodNumbers(a).spent },
  { id: "budget", label: "Budget", numeric: true, defaultVisible: true, value: (a) => a.remaining },
  { id: "target", label: "Target", numeric: false, defaultVisible: true, value: (a) => a.target_label },
  { id: "added", label: "Date Added", numeric: true, defaultVisible: true, value: (a) => a.created_date },
  { id: "buyers", label: "Buyers", numeric: true, defaultVisible: true, extra: true, value: (a) => a.buyers },
  { id: "cpp", label: "Cost/Buyer", numeric: true, defaultVisible: false, extra: true, value: (a) => a.cpp },
  { id: "referral", label: "Referral", numeric: false, defaultVisible: true, extra: true, value: (a) => a.tag_name ?? "" },
  {
    id: "empty",
    label: "Empty for",
    numeric: true,
    defaultVisible: false,
    extra: true,
    value: (a) => (a.remaining > 0.01 ? null : a.empty_days),
  },
];

export const DEFAULT_COLUMNS: ColumnId[] = COLUMNS.filter((c) => c.defaultVisible).map((c) => c.id);

export function sortRows(rows: AdRow[], sort: string, dir: "asc" | "desc"): AdRow[] {
  const col = COLUMNS.find((c) => c.id === sort);
  const val = (a: AdRow): number | string | null => (sort === "ad" ? a.title || a.target_label : col ? col.value(a) : periodNumbers(a).spent);
  const sign = dir === "asc" ? 1 : -1;
  return [...rows].sort((x, y) => {
    const a = val(x);
    const b = val(y);
    if (a === null || a === "") return b === null || b === "" ? 0 : 1; // empty values last, either way
    if (b === null || b === "") return -1;
    if (typeof a === "string" || typeof b === "string") return String(a).localeCompare(String(b)) * sign;
    return (a - b) * sign;
  });
}

const STORAGE_KEY = "tgads.adsColumns.v1";

export function loadColumns(): ColumnId[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_COLUMNS;
    const ids = JSON.parse(raw) as string[];
    const known = ids.filter((x): x is ColumnId => COLUMNS.some((c) => c.id === x));
    return known.length ? known : DEFAULT_COLUMNS;
  } catch {
    return DEFAULT_COLUMNS;
  }
}

export function saveColumns(ids: ColumnId[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(ids));
  } catch {
    /* private mode or blocked storage: the choice just isn't remembered */
  }
}
