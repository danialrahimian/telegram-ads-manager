/** English (Latin digits, Gregorian dates) formatting for the Telegram-Ads-style ads list. */

const int = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const pctFmt = new Intl.NumberFormat("en-US", { style: "percent", maximumFractionDigits: 2 });
const dateFmt = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });

export function enNum(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return int.format(v);
}

/** Money in the ad account currency, e.g. "1.25 TON"; small values get more digits like Telegram Ads. */
export function enMoney(v: number | null | undefined, currency?: string | null): string {
  if (v === null || v === undefined || Number.isNaN(v) || !Number.isFinite(v)) return "—";
  const d = Math.abs(v) < 1 ? 4 : 2;
  const s = new Intl.NumberFormat("en-US", { maximumFractionDigits: d, minimumFractionDigits: 0 }).format(v);
  return currency ? `${s} ${currency.toUpperCase() === "XTR" ? "Stars" : currency.toUpperCase()}` : s;
}

export function enPct(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v) || !Number.isFinite(v)) return "—";
  return pctFmt.format(v);
}

/** "Oct 26, 2026" from unix seconds (or an ISO day). */
export function enDate(v: number | string | null | undefined): string {
  if (v === null || v === undefined || v === "" || v === 0) return "—";
  const d = typeof v === "number" ? new Date(v * 1000) : new Date(v.length === 10 ? `${v}T00:00:00Z` : v);
  return dateFmt.format(d);
}

export function ratio(a: number, b: number): number | null {
  return b ? a / b : null;
}
