import {
  ActivityIcon,
  ArrowLeftIcon,
  BadgeDollarSignIcon,
  MousePointerClickIcon,
  PlayIcon,
  ShoppingCartIcon,
  TargetIcon,
  UserPlusIcon,
  WalletIcon,
} from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";
import { AdStatusBadge } from "@/components/ad-status";
import { useAccountCtx } from "@/components/layout/account-layout";
import { QueryError } from "@/components/query-error";
import { SeriesChart } from "@/components/series-chart";
import { StatCard } from "@/components/stat-card";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { dayLabel, delta, money, num, pct, time, toman } from "@/lib/format";
import { useAds, useDashboard, useLive } from "@/lib/queries";

function tzLabel(tz: string | undefined): string {
  if (!tz || tz === "Asia/Tehran") return "تهران";
  return tz;
}

export default function DashboardPage() {
  const account = useAccountCtx();
  const [win, setWin] = useState<"today" | "7" | "28">("7");
  const [range, setRange] = useState(30);
  const dash = useDashboard(account.slug, Math.max(range, 30));
  const live = useLive(account.slug);
  const ads = useAds(account.slug);
  const cur = account.telegram.currency;

  const s = dash.data?.summary;
  const w = s?.[win];
  const daily = useMemo(() => (dash.data?.daily ?? []).slice(-range), [dash.data, range]);
  const topAds = useMemo(
    () => [...(ads.data?.ads ?? [])].sort((a, b) => b.spent_7 - a.spent_7).filter((a) => a.spent_7 > 0).slice(0, 6),
    [ads.data],
  );

  if (!account.telegram.configured) return null;
  if (dash.error) return <QueryError error={dash.error} retry={() => dash.refetch()} />;

  const target = account.settings.target_cost_per_customer;
  const cpp = (win === "today" ? s?.attributed_today?.cpp : s?.attributed_28.cpp) ?? null;
  const today = live.data?.live.today;
  const tzName = tzLabel(account.settings.bot_timezone);
  const vsLabel = win === "today" ? "نسبت به دیروز تا همین ساعت" : "نسبت به دوره‌ی قبل";

  return (
    <div className="space-y-5">
      {/* Live today */}
      <Card className="gap-3">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <span className="relative flex size-2.5">
              <span className="absolute inline-flex size-full animate-ping rounded-full bg-success opacity-60" />
              <span className="relative inline-flex size-2.5 rounded-full bg-success" />
            </span>
            امروز، زنده
          </CardTitle>
          <CardDescription>از ۰۰:۰۰ به وقت {tzName} — هر چند ثانیه به‌روز می‌شود</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-2">
            <LiveMetric label="خرج" value={money(today?.spent, cur)} loading={live.isLoading} />
            <LiveMetric label="استارت" value={num(today?.starts)} loading={live.isLoading} />
            <LiveMetric label="کلیک" value={num(today?.clicks)} loading={live.isLoading} />
            <LiveMetric label="کاربر جدید ربات" value={num(today?.new_users)} loading={live.isLoading} />
          </div>
          {(live.data?.live.points.length ?? 0) > 1 ? (
            <SeriesChart
              data={live.data!.live.points}
              x="ts"
              xFormat={(v) => time(Number(v))}
              height={170}
              series={[
                { key: "spent", label: "خرج", color: "var(--chart-1)", type: "area", format: (v) => money(v) },
                { key: "starts", label: "استارت", color: "var(--chart-2)", type: "line", axis: "secondary" },
              ]}
            />
          ) : (
            <div className="flex h-[170px] items-center justify-center rounded-lg border border-dashed text-sm text-muted-foreground">
              نمودار امروز بعد از چند همگام‌سازی ظاهر می‌شود.
            </div>
          )}
        </CardContent>
      </Card>

      {/* KPI cards */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold">خلاصه‌ی عملکرد</h2>
        <Tabs value={win} onValueChange={(v) => setWin(v as "today" | "7" | "28")}>
          <TabsList>
            <TabsTrigger value="today">امروز</TabsTrigger>
            <TabsTrigger value="7">۷ روز</TabsTrigger>
            <TabsTrigger value="28">۲۸ روز</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard
          label="خرج تبلیغات"
          icon={WalletIcon}
          loading={!w}
          value={money(w?.spent, cur)}
          change={w ? delta(w.spent, w.prev_spent) : null}
          goodWhen="neutral"
          hint={win === "today" ? `از ۰۰:۰۰ به وقت ${tzName} · ${vsLabel}` : vsLabel}
        />
        <StatCard
          label="استارت ربات"
          icon={PlayIcon}
          loading={!w}
          value={num(w?.starts)}
          change={w ? delta(w.starts, w.prev_starts) : null}
          hint={w?.start_rate != null ? `${pct(w.start_rate)} از کلیک‌ها` : undefined}
        />
        <StatCard
          label="هزینه هر استارت"
          icon={TargetIcon}
          loading={!w}
          value={money(w?.cps, cur)}
          change={w ? delta(w.cps, w.prev_cps) : null}
          goodWhen="down"
          tone={w?.cps != null && w.cps > account.settings.stop_cost_per_start ? "bad" : undefined}
          hint={`سقف: ${money(account.settings.stop_cost_per_start, cur)}`}
        />
        <StatCard
          label="نرخ کلیک (CTR)"
          icon={MousePointerClickIcon}
          loading={!w}
          value={pct(w?.ctr)}
          hint={w ? `${num(w.views)} نمایش` : undefined}
        />
        <StatCard
          label="خریدار"
          icon={ShoppingCartIcon}
          loading={!w}
          value={num(w?.buyers)}
          change={w ? delta(w.buyers, w.prev_buyers) : null}
          hint={w ? `${num(w.purchases)} خرید` : undefined}
        />
        <StatCard
          label="فروش ربات"
          icon={BadgeDollarSignIcon}
          loading={!w}
          value={w ? toman(w.revenue) : "—"}
          change={w ? delta(w.revenue, w.prev_revenue) : null}
          hint={account.bot.configured ? undefined : "ربات وصل نیست"}
        />
        <StatCard
          label="کاربر جدید ربات"
          icon={UserPlusIcon}
          loading={!w}
          value={num(w?.new_users)}
          hint="از آمار ربات"
        />
        <StatCard
          label="هزینه هر مشتری (رفرال)"
          icon={ActivityIcon}
          loading={!s}
          value={money(cpp, cur)}
          tone={cpp == null ? undefined : cpp <= target ? "good" : "bad"}
          hint={`هدف: ${money(target, cur)} · ${win === "today" ? "امروز" : "۲۸ روز"}`}
        />
      </div>

      {/* Charts */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">روند روزانه</CardTitle>
          <CardDescription>خرج و استارت ربات در هر روز</CardDescription>
          <CardAction>
            <Tabs value={String(range)} onValueChange={(v) => setRange(Number(v))}>
              <TabsList>
                <TabsTrigger value="14">۱۴</TabsTrigger>
                <TabsTrigger value="30">۳۰</TabsTrigger>
                <TabsTrigger value="60">۶۰ روز</TabsTrigger>
              </TabsList>
            </Tabs>
          </CardAction>
        </CardHeader>
        <CardContent>
          {dash.isLoading ? (
            <Skeleton className="h-[260px]" />
          ) : (
            <SeriesChart
              data={daily}
              x="day"
              xFormat={dayLabel}
              series={[
                { key: "spent", label: "خرج", color: "var(--chart-1)", type: "bar", format: (v) => money(v) },
                { key: "starts", label: "استارت", color: "var(--chart-2)", type: "line", axis: "secondary" },
              ]}
            />
          )}
        </CardContent>
      </Card>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">فروش و کاربر جدید</CardTitle>
            <CardDescription>خریدهای ثبت‌شده در ربات و کاربرانی که از رفرال آگهی‌ها آمدند</CardDescription>
          </CardHeader>
          <CardContent>
            {account.bot.configured ? (
              <SeriesChart
                data={daily}
                x="day"
                xFormat={dayLabel}
                height={220}
                series={[
                  { key: "purchases", label: "همه‌ی خریدها", color: "var(--chart-5)", type: "bar" },
                  { key: "attributed_purchases", label: "خرید از رفرال", color: "var(--chart-3)", type: "bar" },
                  { key: "new_users", label: "کاربر جدید", color: "var(--chart-2)", type: "line", axis: "secondary" },
                ]}
              />
            ) : (
              <div className="flex h-[220px] flex-col items-center justify-center gap-2 rounded-lg border border-dashed text-center text-sm text-muted-foreground">
                کلید API ربات را وصل کنید تا فروش اینجا دیده شود.
                <Button asChild size="sm" variant="outline">
                  <Link to={`/accounts/${account.slug}/settings`}>اتصال ربات</Link>
                </Button>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">بودجه</CardTitle>
            <CardDescription>موجودی حساب و بودجه‌ی روی آگهی‌ها</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {s ? (
              <>
                <BudgetRow label="موجودی آزاد حساب" value={money(s.balance.account, cur)} />
                <BudgetRow label="بودجه روی همه‌ی آگهی‌ها" value={money(s.balance.on_ads, cur)} />
                <BudgetRow label="روی آگهی‌های در حال اجرا" value={money(s.balance.on_running_ads, cur)} />
                <BudgetRow label="کل خرج تا امروز" value={money(s.balance.spent_total, cur)} />
                <div className="space-y-1.5 pt-1">
                  <div className="flex justify-between text-xs text-muted-foreground">
                    <span>سهم بودجه‌ی فعال</span>
                    <span className="num">
                      {pct(s.balance.on_ads ? s.balance.on_running_ads / s.balance.on_ads : 0)}
                    </span>
                  </div>
                  <Progress value={s.balance.on_ads ? (s.balance.on_running_ads / s.balance.on_ads) * 100 : 0} />
                </div>
                <div className="grid grid-cols-3 gap-2 pt-1 text-center text-xs">
                  <CountBox label="فعال" value={s.counts.running} />
                  <CountBox label="در بررسی" value={s.counts.review} />
                  <CountBox label="رد شده" value={s.counts.declined} />
                </div>
              </>
            ) : (
              <Skeleton className="h-48" />
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">پرخرج‌ترین آگهی‌های ۷ روز اخیر</CardTitle>
          <CardAction>
            <Button asChild size="sm" variant="ghost">
              <Link to={`/accounts/${account.slug}/ads`}>
                همه‌ی آگهی‌ها <ArrowLeftIcon />
              </Link>
            </Button>
          </CardAction>
        </CardHeader>
        <CardContent className="grid gap-2">
          {ads.isLoading && <Skeleton className="h-24" />}
          {!ads.isLoading && topAds.length === 0 && (
            <p className="text-sm text-muted-foreground">در ۷ روز اخیر خرجی ثبت نشده است.</p>
          )}
          {topAds.map((a) => (
            <Link
              key={a.ad_id}
              to={`/accounts/${account.slug}/ads/${a.ad_id}`}
              className="flex flex-col gap-2 rounded-lg border px-3 py-2.5 transition-colors hover:bg-muted/50 sm:flex-row sm:items-center sm:gap-3"
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm font-medium">{a.target_label || a.title || `#${a.ad_id}`}</span>
                  <span className="shrink-0 sm:hidden">
                    <AdStatusBadge status={a.status} paused={a.is_paused} />
                  </span>
                </div>
                <div className="truncate text-xs text-muted-foreground">{a.text}</div>
              </div>
              <div className="flex items-center gap-4 text-xs sm:gap-3">
                <div className="sm:text-end">
                  <span className="text-muted-foreground sm:hidden">خرج: </span>
                  <span className="num font-medium">{money(a.spent_7, cur)}</span>
                  <div className="hidden text-muted-foreground sm:block">خرج</div>
                </div>
                <div className="sm:text-end">
                  <span className="text-muted-foreground sm:hidden">هر استارت: </span>
                  <span className="num font-medium">{money(a.cps_7, cur)}</span>
                  <div className="hidden text-muted-foreground sm:block">هر استارت</div>
                </div>
                <span className="hidden sm:inline-flex">
                  <AdStatusBadge status={a.status} paused={a.is_paused} />
                </span>
              </div>
            </Link>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}

function LiveMetric({ label, value, loading }: { label: string; value: string; loading?: boolean }) {
  return (
    <div className="rounded-lg bg-muted/50 px-3 py-2.5">
      <div className="text-xs text-muted-foreground">{label}</div>
      {loading ? <Skeleton className="mt-1 h-6 w-16" /> : <div className="num text-lg font-bold">{value}</div>}
    </div>
  );
}

function BudgetRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-2 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="num font-semibold">{value}</span>
    </div>
  );
}

function CountBox({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border px-2 py-2">
      <div className="num text-base font-bold">{num(value)}</div>
      <div className="text-muted-foreground">{label}</div>
    </div>
  );
}
