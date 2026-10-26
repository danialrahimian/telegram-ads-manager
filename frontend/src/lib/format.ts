/** Persian number/date formatting helpers. All amounts from the API are plain numbers. */

const faInt = new Intl.NumberFormat("fa-IR", { maximumFractionDigits: 0 });
const faPct = new Intl.NumberFormat("fa-IR", { style: "percent", maximumFractionDigits: 1 });

export function num(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  if (digits === 0) return faInt.format(v);
  return new Intl.NumberFormat("fa-IR", { maximumFractionDigits: digits }).format(v);
}

/** Money in the ad account currency (TON/EUR/Stars) — small values need more digits. */
export function money(v: number | null | undefined, currency?: string | null, digits?: number): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const d = digits ?? (Math.abs(v) < 1 ? 4 : Math.abs(v) < 100 ? 2 : 0);
  const s = new Intl.NumberFormat("fa-IR", { maximumFractionDigits: d, minimumFractionDigits: 0 }).format(v);
  return currency ? `${s} ${currencyLabel(currency)}` : s;
}

/** Latin digits in a server-written Persian sentence shown as Persian digits (the decimal point too). */
export function faDigits(s: string): string {
  return s.replace(/(\d)\.(\d)/g, "$1٫$2").replace(/\d/g, (d) => "۰۱۲۳۴۵۶۷۸۹"[Number(d)]);
}

export function currencyLabel(c?: string | null): string {
  if (!c) return "";
  const u = c.toUpperCase();
  if (u === "TON") return "TON";
  if (u === "EUR") return "€";
  if (u === "XTR" || u === "STARS") return "⭐";
  return u;
}

export function toman(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `${faInt.format(v)} تومان`;
}

export function pct(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return faPct.format(v);
}

/** Change between two values as a signed percentage, or null when it can't be computed. */
export function delta(cur: number | null | undefined, prev: number | null | undefined): number | null {
  if (cur === null || cur === undefined || prev === null || prev === undefined || prev === 0) return null;
  return (cur - prev) / Math.abs(prev);
}

const dateFmt = new Intl.DateTimeFormat("fa-IR-u-ca-persian", { year: "numeric", month: "long", day: "numeric" });
const shortDate = new Intl.DateTimeFormat("fa-IR-u-ca-persian", { month: "short", day: "numeric" });
const dateTimeFmt = new Intl.DateTimeFormat("fa-IR-u-ca-persian", {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});
const timeFmt = new Intl.DateTimeFormat("fa-IR", { hour: "2-digit", minute: "2-digit" });

function toDate(v: number | string): Date {
  if (typeof v === "number") return new Date(v < 1e12 ? v * 1000 : v);
  return new Date(v.length === 10 ? `${v}T00:00:00Z` : v);
}

export function date(v: number | string | null | undefined): string {
  if (v === null || v === undefined || v === "" || v === 0) return "—";
  return dateFmt.format(toDate(v));
}

export function dayLabel(v: string): string {
  return shortDate.format(toDate(v));
}

export function dateTime(v: number | string | null | undefined): string {
  if (v === null || v === undefined || v === "" || v === 0) return "—";
  return dateTimeFmt.format(toDate(v));
}

export function time(v: number | null | undefined): string {
  if (!v) return "—";
  return timeFmt.format(toDate(v));
}

/** "۲ دقیقه پیش" style relative time from a unix timestamp (seconds). */
export function ago(ts: number | null | undefined, now = Date.now() / 1000): string {
  if (!ts) return "هرگز";
  const s = Math.max(0, Math.round(now - ts));
  if (s < 10) return "همین حالا";
  if (s < 60) return `${num(s)} ثانیه پیش`;
  const m = Math.round(s / 60);
  if (m < 60) return `${num(m)} دقیقه پیش`;
  const h = Math.round(m / 60);
  if (h < 48) return `${num(h)} ساعت پیش`;
  return `${num(Math.round(h / 24))} روز پیش`;
}

export function seconds(s: number): string {
  if (s < 60) return `${num(s)} ثانیه`;
  if (s < 3600) return `${num(Math.round(s / 60))} دقیقه`;
  return `${num(Math.round(s / 360) / 10, 1)} ساعت`;
}
