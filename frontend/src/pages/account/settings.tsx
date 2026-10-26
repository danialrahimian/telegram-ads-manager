import { useQueryClient } from "@tanstack/react-query";
import {
  BotIcon,
  CheckCircle2Icon,
  CheckIcon,
  CircleXIcon,
  KeyRoundIcon,
  LinkIcon,
  OctagonXIcon,
  RefreshCwIcon,
  SaveIcon,
  ServerIcon,
  TimerIcon,
  Trash2Icon,
  WalletIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";
import { BotDiagnostics } from "@/components/bot-diagnostics";
import { CopyButton } from "@/components/copy-button";
import { Field } from "@/components/field";
import { HealthBadge } from "@/components/health";
import { useAccountCtx } from "@/components/layout/account-layout";
import { PageHeader } from "@/components/page-header";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { api, errorMessage } from "@/lib/api";
import { ago, money, num, seconds } from "@/lib/format";
import { accountApi, keys, useAction, useSystem, useTracking } from "@/lib/queries";
import type { Account, AccountSettings, BotCheck, TelegramCheck } from "@/lib/types";
import { cn } from "@/lib/utils";
import { COLORS } from "../account-new";

export default function AccountSettingsPage() {
  const account = useAccountCtx();
  useEffect(() => {
    if (window.location.hash) document.querySelector(window.location.hash)?.scrollIntoView({ behavior: "smooth" });
  }, []);
  return (
    <div className="space-y-5">
      <PageHeader
        title="اتصال و تنظیمات"
        description="توکن Telegram Ads و کلید API ربات همین حساب را وارد کنید؛ سلامت هر اتصال جدا بررسی می‌شود."
      />
      <div className="grid gap-5 xl:grid-cols-2">
        <TelegramCard account={account} />
        <BotCard account={account} />
      </div>
      <SyncCard account={account} />
      <SettingsGroup
        id="rules"
        account={account}
        title="هدف‌ها و قوانین پیشنهاد"
        description={`مبلغ‌ها به ${account.telegram.currency || "ارز حساب"} هستند. قوانین فقط پیشنهاد می‌سازند؛ اجرا با تأیید شماست.`}
        fields={[
          { key: "target_cost_per_customer", label: "هدف هزینه‌ی هر مشتری", step: 0.001 },
          { key: "stop_cost_per_start", label: "سقف هزینه‌ی هر استارت (پیشنهاد توقف)", step: 0.001 },
          { key: "scale_cost_per_start", label: "هزینه‌ی هر استارت خوب (پیشنهاد شارژ)", step: 0.001 },
          { key: "min_spend_to_judge", label: "حداقل خرج برای قضاوت", step: 0.01 },
          { key: "min_purchases_to_judge", label: "حداقل خرید برای قضاوت با فروش", step: 1 },
          { key: "topup_max_per_ad", label: "سقف هر شارژ", step: 0.1 },
          { key: "topup_daily_cap", label: "سقف شارژ روزانه", step: 0.1 },
          { key: "stalled_spend_28d", label: "خرج کمتر از این در ۲۸ روز = آگهی بی‌تحرک", step: 0.001 },
          { key: "stalled_min_remaining", label: "حداقل بودجه‌ی مانده برای پیشنهاد برداشت", step: 0.01 },
          { key: "stalled_min_age_days", label: "حداقل عمر آگهی برای برداشت (روز)", step: 1 },
          { key: "fatigue_age_days", label: "حداقل عمر برای تشخیص خستگی (روز)", step: 1 },
          { key: "fatigue_ratio", label: "نسبت افت برای خستگی (مثلاً ۱٫۵ برابر گران‌تر)", step: 0.1 },
          { key: "refill_max_empty_days", label: "حداکثر روزهای بی‌بودجه برای پیشنهاد شارژ دوباره", step: 1 },
          { key: "replace_zero_view_days", label: "روزهای پیاپی بدون نمایش (با بودجه) = پیشنهاد جایگزینی", step: 1 },
        ]}
      />
      <TrackingCard account={account} />
      <SettingsGroup
        id="bulk"
        account={account}
        title="عملیات گروهی"
        description="برای اینکه محدودیت نرخ تلگرام نخورد و همه‌ی آگهی‌ها هم‌زمان به بررسی نروند، آگهی‌ها دسته‌دسته فرستاده می‌شوند."
        fields={[
          { key: "bulk_batch_size", label: "تعداد آگهی در هر دسته", step: 1 },
          { key: "bulk_batch_interval_seconds", label: "فاصله‌ی دسته‌ها (ثانیه)", step: 10 },
          { key: "bulk_max_in_review", label: "حداکثر آگهی هم‌زمان در بررسی", step: 1 },
          { key: "min_initial_budget", label: "حداقل بودجه‌ی اولیه‌ی آگهی (حداقل تلگرام: ۱ TON)", step: 0.1 },
          { key: "bulk_daily_new_budget_cap", label: "سقف روزانه‌ی بودجه‌ی اولیه‌ی آگهی‌های جایگزین", step: 1 },
        ]}
      />
      <div className="grid gap-5 xl:grid-cols-2">
        <SettingsGroup
          id="defaults"
          account={account}
          title="پیش‌فرض آگهی جدید"
          fields={[
            { key: "default_cpm_channel", label: "CPM کانال", step: 0.01 },
            { key: "default_cpm_bot", label: "CPM ربات", step: 0.01 },
            { key: "default_budget", label: "بودجه‌ی اولیه", step: 0.1 },
          ]}
        />
        <BotSettingsCard account={account} />
      </div>
      <GeneralCard account={account} />
      <DangerCard account={account} />
    </div>
  );
}

// ---------------------------------------------------------------- Telegram Ads

function TelegramCard({ account }: { account: Account }) {
  const tg = account.telegram;
  const base = accountApi(account.slug);
  const sys = useSystem();
  const [token, setToken] = useState("");
  const [result, setResult] = useState<TelegramCheck | null>(null);
  const inv = [keys.acc(account.slug), keys.accounts, keys.overview];

  const save = useAction<void, { check: TelegramCheck }>({
    fn: () => api.put(`${base}/telegram`, { token: token.trim() }),
    invalidate: inv,
    success: (r) => (r.check.ok ? "توکن ذخیره شد و اتصال سالم است." : null),
    onSuccess: (r) => {
      setResult(r.check);
      setToken("");
      if (!r.check.ok) toast.error(r.check.error ?? "اتصال برقرار نشد");
    },
  });
  const check = useAction<void, { check: TelegramCheck }>({
    fn: () => api.post(`${base}/telegram/check`),
    invalidate: inv,
    success: (r) => (r.check.ok ? "اتصال سالم است." : null),
    onSuccess: (r) => {
      setResult(r.check);
      if (!r.check.ok) toast.error(r.check.error ?? "اتصال برقرار نشد");
    },
  });
  const remove = useAction<void>({ fn: () => api.del(`${base}/telegram`), invalidate: inv, success: "توکن حذف شد." });

  return (
    <Card id="telegram">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <WalletIcon className="size-4" /> Telegram Ads API
        </CardTitle>
        <CardDescription>توکن از ads.telegram.org ← حساب ← API گرفته می‌شود.</CardDescription>
        <CardAction>
          <HealthBadge status={tg.status} configured={tg.configured} />
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-4">
        {tg.configured && (
          <div className="grid grid-cols-2 gap-3 rounded-lg bg-muted/50 p-3 text-sm">
            <Info label="حساب" value={tg.title || "—"} />
            <Info label="توکن" value={<span className="ltr">•••• {tg.token_last4}</span>} />
            <Info label="موجودی" value={money(tg.remaining_budget, tg.currency)} />
            <Info label="آخرین بررسی" value={ago(tg.checked_ts)} />
          </div>
        )}
        {tg.error && tg.status !== "ok" && (
          <Alert variant="destructive">
            <CircleXIcon />
            <AlertDescription>{tg.error}</AlertDescription>
          </Alert>
        )}
        {result?.ok && (
          <Alert variant="success">
            <CheckCircle2Icon />
            <AlertDescription>
              وصل شد: {result.title} — موجودی {money(result.remaining_budget, result.currency)}
            </AlertDescription>
          </Alert>
        )}
        <form
          className="grid gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (token.trim().length >= 8) save.mutate();
          }}
        >
          <Field
            label={tg.configured ? "توکن تازه (برای جایگزینی)" : "توکن API"}
            htmlFor="tg-token"
            hint="توکن رمزنگاری‌شده ذخیره می‌شود و دیگر کامل نمایش داده نمی‌شود. آن را جای دیگری (مثل چت) نفرستید."
          >
            <Input
              id="tg-token"
              type="password"
              dir="ltr"
              autoComplete="off"
              spellCheck={false}
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="••••••••"
            />
          </Field>
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={save.isPending || token.trim().length < 8}>
              <KeyRoundIcon /> {save.isPending ? "در حال بررسی…" : "ذخیره و بررسی"}
            </Button>
            {tg.configured && (
              <Button type="button" variant="outline" onClick={() => check.mutate()} disabled={check.isPending}>
                <RefreshCwIcon className={check.isPending ? "animate-spin" : ""} /> بررسی سلامت
              </Button>
            )}
            {tg.configured && (
              <Button type="button" variant="ghost" className="text-destructive" onClick={() => remove.mutate()}>
                <Trash2Icon /> حذف توکن
              </Button>
            )}
          </div>
        </form>
      </CardContent>
      <CardFooter className="block border-t pt-4 text-xs leading-relaxed text-muted-foreground">
        <ServerIcon className="me-1.5 inline size-3.5 align-[-2px]" />
        اگر تلگرام خطای IP داد، آی‌پی سرور را در تنظیمات API تلگرام مجاز کنید:
        {sys.data?.server_ip ? (
          <span className="ms-1 inline-flex items-center">
            <code className="ltr rounded bg-muted px-1.5 py-0.5">{sys.data.server_ip}</code>
            <CopyButton value={sys.data.server_ip} />
          </span>
        ) : (
          <span className="ms-1">{sys.isLoading ? "…" : "پیدا نشد (روی سرور: curl ifconfig.me)"}</span>
        )}
      </CardFooter>
    </Card>
  );
}

// ---------------------------------------------------------------- Sales bot

function BotCard({ account }: { account: Account }) {
  const bot = account.bot;
  const base = accountApi(account.slug);
  const [url, setUrl] = useState(bot.base_url || "");
  const [username, setUsername] = useState(bot.username || "");
  const [key, setKey] = useState("");
  const [result, setResult] = useState<BotCheck | null>(null);
  const inv = [keys.acc(account.slug), keys.accounts, keys.overview];

  const save = useAction<void, { check: BotCheck }>({
    fn: () =>
      api.put(`${base}/bot`, {
        base_url: url.trim(),
        username: username.trim(),
        ...(key.trim() ? { api_key: key.trim() } : {}),
      }),
    invalidate: inv,
    success: (r) => (r.check.status === "ok" ? "اتصال ربات ذخیره شد و سالم است." : "ذخیره شد."),
    onSuccess: (r) => {
      setResult(r.check);
      setKey("");
    },
  });
  const check = useAction<void, { check: BotCheck }>({
    fn: () => api.post(`${base}/bot/check`),
    invalidate: inv,
    success: (r) => (r.check.status === "ok" ? "اتصال ربات سالم است." : null),
    onSuccess: (r) => setResult(r.check),
  });
  const checks = result?.checks;
  const stats = result?.stats ?? bot.stats;
  const fieldList = Object.entries(bot.fields ?? {}).filter(([k, v]) => v?.length && k !== "transactions_mode");
  const perType = bot.fields?.transactions_mode?.[0] === "per_type";

  return (
    <Card id="bot">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <BotIcon className="size-4" /> API ربات فروش
        </CardTitle>
        <CardDescription>آدرس پنل ربات و کلید API اختصاصی همین ربات (هر ربات کلید جدا دارد).</CardDescription>
        <CardAction>
          <HealthBadge status={bot.status} configured={bot.configured} />
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-4">
        {bot.configured && (
          <div className="grid grid-cols-2 gap-3 rounded-lg bg-muted/50 p-3 text-sm">
            <Info label="کاربران ربات" value={num(Number(stats?.count_user ?? 0))} />
            <Info label="سرویس‌ها" value={num(Number(stats?.count_invoice ?? 0))} />
            <Info label="کلید" value={<span className="ltr">•••• {bot.key_last4}</span>} />
            <Info label="آخرین بررسی" value={ago(bot.checked_ts)} />
          </div>
        )}
        {checks && checks.length > 0 && (
          <ul className="space-y-1.5">
            {checks.map((c) => (
              <li key={c.name} className="flex items-start gap-2 text-sm">
                {c.ok ? (
                  <CheckCircle2Icon className="mt-0.5 size-4 shrink-0 text-success" />
                ) : (
                  <CircleXIcon className="mt-0.5 size-4 shrink-0 text-destructive" />
                )}
                <span>
                  <span className="font-medium">{c.label}:</span>{" "}
                  <span className="text-muted-foreground">{c.message}</span>
                </span>
              </li>
            ))}
          </ul>
        )}
        {!checks && bot.error && bot.status !== "ok" && (
          <Alert variant={bot.status === "warn" ? "warning" : "destructive"}>
            <CircleXIcon />
            <AlertDescription>{bot.error}</AlertDescription>
          </Alert>
        )}
        <form
          className="grid gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          <Field label="آدرس API ربات" htmlFor="bot-url" hint="آدرس صفحه‌ی docs هم قبول است، مثل https://bot.example.com/api/docs">
            <Input
              id="bot-url"
              dir="ltr"
              className="text-left"
              placeholder="https://bot.example.com"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
          </Field>
          <Field label="نام کاربری ربات" htmlFor="bot-user" hint="برای ساخت لینک آگهی (t.me/…)">
            <Input
              id="bot-user"
              dir="ltr"
              className="text-left"
              placeholder="sychonetbot"
              value={username}
              onChange={(e) => setUsername(e.target.value.replace(/^@/, ""))}
            />
          </Field>
          <Field
            label={bot.configured ? "کلید API تازه (خالی = همان قبلی)" : "کلید API ربات"}
            htmlFor="bot-key"
            hint="کلید باید دسترسی users و statbot داشته باشد."
          >
            <Input
              id="bot-key"
              type="password"
              dir="ltr"
              autoComplete="off"
              spellCheck={false}
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder="••••••••"
            />
          </Field>
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={save.isPending || !url.trim() || (!bot.configured && !key.trim())}>
              <SaveIcon /> {save.isPending ? "در حال بررسی…" : "ذخیره و بررسی"}
            </Button>
            {bot.configured && (
              <Button type="button" variant="outline" onClick={() => check.mutate()} disabled={check.isPending}>
                <RefreshCwIcon className={check.isPending ? "animate-spin" : ""} /> بررسی سلامت
              </Button>
            )}
          </div>
        </form>
        {perType && (
          <Alert variant="warning">
            <AlertDescription>
              گزارش کامل تراکنش‌ها در این ربات خطا می‌دهد؛ فروش فقط برای نوع‌های خرید (
              <span className="ltr">{account.settings.purchase_types.join(", ")}</span>) جداگانه خوانده می‌شود. جدول
              «تراکنش‌ها بر اساس نوع» هم فقط همین نوع‌ها را نشان می‌دهد.
            </AlertDescription>
          </Alert>
        )}
        {bot.configured && <BotDiagnostics account={account} />}
        {fieldList.length > 0 && (
          <details className="rounded-lg border px-3 py-2 text-xs">
            <summary className="cursor-pointer text-muted-foreground">فیلدهای شناسایی‌شده در پاسخ ربات</summary>
            <div className="mt-2 space-y-1.5">
              {fieldList.map(([k, v]) => (
                <div key={k}>
                  <span className="font-medium">{k}: </span>
                  <span className="ltr text-muted-foreground">{v.join(", ")}</span>
                </div>
              ))}
            </div>
          </details>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------- settings helpers

function useSaveSettings(account: Account) {
  return useAction<Partial<AccountSettings> & Record<string, unknown>>({
    fn: (patch) => api.patch(accountApi(account.slug), { settings: patch }),
    invalidate: [keys.acc(account.slug), keys.accounts],
    success: "تنظیمات ذخیره شد.",
  });
}

type NumKey = { [K in keyof AccountSettings]: AccountSettings[K] extends number ? K : never }[keyof AccountSettings];

function SettingsGroup({
  id,
  account,
  title,
  description,
  fields,
}: {
  id: string;
  account: Account;
  title: string;
  description?: string;
  fields: { key: NumKey; label: string; step: number }[];
}) {
  const initial = () => Object.fromEntries(fields.map((f) => [f.key, String(account.settings[f.key])]));
  const [vals, setVals] = useState<Record<string, string>>(initial);
  const snapshot = fields.map((f) => account.settings[f.key]).join("|");
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => setVals(initial()), [snapshot]);
  const save = useSaveSettings(account);
  const changed = fields.filter((f) => vals[f.key] !== String(account.settings[f.key]));
  const invalid = fields.some((f) => vals[f.key] === "" || Number.isNaN(Number(vals[f.key])) || Number(vals[f.key]) < 0);

  return (
    <Card id={id}>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
        {description && <CardDescription>{description}</CardDescription>}
      </CardHeader>
      <CardContent className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {fields.map((f) => (
          <Field key={f.key} label={f.label} htmlFor={`${id}-${f.key}`}>
            <Input
              id={`${id}-${f.key}`}
              type="number"
              inputMode="decimal"
              dir="ltr"
              step={f.step}
              min={0}
              value={vals[f.key]}
              onChange={(e) => setVals((v) => ({ ...v, [f.key]: e.target.value }))}
            />
          </Field>
        ))}
      </CardContent>
      <CardFooter className="justify-end gap-2">
        {changed.length > 0 && (
          <Button variant="ghost" onClick={() => setVals(initial())}>
            بازگردانی
          </Button>
        )}
        <Button
          disabled={!changed.length || invalid || save.isPending}
          onClick={() => save.mutate(Object.fromEntries(changed.map((f) => [f.key, Number(vals[f.key])])))}
        >
          <SaveIcon /> ذخیره
        </Button>
      </CardFooter>
    </Card>
  );
}

// ---------------------------------------------------------------- sync cadence

function SyncCard({ account }: { account: Account }) {
  const sys = useSystem();
  const floorTest = (sys.data?.min_sync_seconds ?? 10) < 10 ? sys.data!.min_sync_seconds : null;
  const items = [
    { key: "sync_fast_seconds" as const, label: "آگهی‌ها و موجودی", desc: "وضعیت، خرج و بودجه‌ی همه‌ی آگهی‌ها", min: 10 },
    { key: "sync_stats_seconds" as const, label: "آمار روزانه", desc: "آمار روز به روز آگهی‌های فعال (سنگین‌تر)", min: 120 },
    { key: "sync_bot_seconds" as const, label: "ربات فروش", desc: "کاربران، تراکنش‌ها و رفرال‌ها", min: 30 },
  ];
  const [vals, setVals] = useState(() => Object.fromEntries(items.map((i) => [i.key, String(account.settings[i.key])])));
  const snapshot = items.map((i) => account.settings[i.key]).join("|");
  useEffect(
    () => setVals(Object.fromEntries(items.map((i) => [i.key, String(account.settings[i.key])]))),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [snapshot],
  );
  const save = useSaveSettings(account);
  const changed = items.filter((i) => vals[i.key] !== String(account.settings[i.key]));
  const PRESETS = [10, 15, 30, 60, 120, 300, 600, 1800];

  return (
    <Card id="sync">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <TimerIcon className="size-4" /> همگام‌سازی دوره‌ای
        </CardTitle>
        <CardDescription>
          اطلاعات پنل هر چند ثانیه یک بار از تلگرام و ربات خوانده می‌شود. فاصله‌ی کمتر یعنی داده‌ی تازه‌تر و درخواست بیشتر.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-5 md:grid-cols-3">
        {items.map((i) => {
          const min = floorTest ?? i.min;
          const v = Number(vals[i.key]);
          const st = account.sync[i.key === "sync_fast_seconds" ? "fast" : i.key === "sync_stats_seconds" ? "stats" : "bot"];
          return (
            <div key={i.key} className="space-y-2 rounded-lg border p-3">
              <div>
                <div className="text-sm font-medium">{i.label}</div>
                <div className="text-xs text-muted-foreground">{i.desc}</div>
              </div>
              <div className="flex items-center gap-2">
                <Input
                  type="number"
                  dir="ltr"
                  min={min}
                  step={1}
                  value={vals[i.key]}
                  onChange={(e) => setVals((x) => ({ ...x, [i.key]: e.target.value }))}
                  className="w-28"
                  aria-label={`${i.label} (ثانیه)`}
                />
                <span className="text-xs text-muted-foreground">ثانیه {v >= 60 && `(${seconds(v)})`}</span>
              </div>
              <div className="flex flex-wrap gap-1">
                {PRESETS.filter((p) => p >= min).slice(0, 5).map((p) => (
                  <button
                    key={p}
                    type="button"
                    onClick={() => setVals((x) => ({ ...x, [i.key]: String(p) }))}
                    className={cn(
                      "num rounded-md border px-2 py-0.5 text-xs transition hover:bg-muted",
                      v === p && "border-primary bg-accent text-accent-foreground",
                    )}
                  >
                    {seconds(p)}
                  </button>
                ))}
              </div>
              {v < min && <p className="text-xs text-warning">حداقل {seconds(min)}؛ مقدار کمتر به حداقل گرد می‌شود.</p>}
              <p className="text-[11px] text-muted-foreground">
                آخرین اجرا: {ago(st?.last_ok)} {st?.duration != null && <span className="num">({num(st.duration, 1)} ثانیه)</span>}
                {st?.error && <span className="block text-destructive">{st.error}</span>}
              </p>
            </div>
          );
        })}
      </CardContent>
      <CardFooter className="justify-between gap-2">
        <span className="text-xs text-muted-foreground">
          صفحه‌ی داشبورد هم هر {num(sys.data?.ui_refresh_seconds ?? 5)} ثانیه تازه می‌شود (تنظیمات کلی).
        </span>
        <Button
          disabled={!changed.length || save.isPending || changed.some((i) => !(Number(vals[i.key]) > 0))}
          onClick={() => save.mutate(Object.fromEntries(changed.map((i) => [i.key, Number(vals[i.key])])))}
        >
          <SaveIcon /> ذخیره
        </Button>
      </CardFooter>
    </Card>
  );
}

// ---------------------------------------------------------------- bot-specific settings

function BotSettingsCard({ account }: { account: Account }) {
  const st = account.settings;
  const [tz, setTz] = useState(st.bot_timezone);
  const [types, setTypes] = useState(st.purchase_types.join(", "));
  const [rate, setRate] = useState(String(st.toman_per_unit));
  const save = useSaveSettings(account);
  const parsed = types
    .split(/[,،\s]+/)
    .map((x) => x.trim())
    .filter(Boolean);
  const known = Object.values(account.bot.fields ?? {}).flat();
  return (
    <Card id="bot-settings">
      <CardHeader>
        <CardTitle className="text-base">تنظیمات فروش ربات</CardTitle>
        <CardDescription>کدام تراکنش‌ها خرید حساب شوند و ارزش هر واحد ارز تبلیغ به تومان.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <Field label="نوع تراکنش‌های خرید" htmlFor="ptypes" hint="با کاما جدا کنید؛ مثل buy, buy_extend">
          <Input id="ptypes" dir="ltr" className="text-left" value={types} onChange={(e) => setTypes(e.target.value)} />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="منطقه‌ی زمانی ربات" htmlFor="tz" hint="برای خواندن درست زمان تراکنش‌ها">
            <Input id="tz" dir="ltr" className="text-left" value={tz} onChange={(e) => setTz(e.target.value)} />
          </Field>
          <Field
            label={`قیمت هر ${account.telegram.currency || "واحد"} به تومان`}
            htmlFor="rate"
            hint="برای محاسبه‌ی بازگشت هزینه‌ی تبلیغ (ROAS). ۰ = محاسبه نشود"
          >
            <Input id="rate" type="number" dir="ltr" min={0} value={rate} onChange={(e) => setRate(e.target.value)} />
          </Field>
        </div>
        {known.length === 0 && (
          <p className="text-xs text-muted-foreground">بعد از اولین همگام‌سازی ربات، فیلدهای پاسخ در کارت اتصال نمایش داده می‌شوند.</p>
        )}
      </CardContent>
      <CardFooter className="justify-end">
        <Button
          disabled={save.isPending || !parsed.length || !tz.trim() || Number(rate) < 0}
          onClick={() =>
            save.mutate({ purchase_types: parsed, bot_timezone: tz.trim(), toman_per_unit: Number(rate) || 0 })
          }
        >
          <SaveIcon /> ذخیره
        </Button>
      </CardFooter>
    </Card>
  );
}

// ---------------------------------------------------------------- referral tracking (referrer pool)

function TrackingCard({ account }: { account: Account }) {
  const tracking = useTracking(account.slug);
  const [ids, setIds] = useState(account.settings.referrer_pool.join("\n"));
  const snapshot = account.settings.referrer_pool.join(",");
  useEffect(() => setIds(account.settings.referrer_pool.join("\n")), [snapshot]); // eslint-disable-line react-hooks/exhaustive-deps
  const base = accountApi(account.slug);
  const parsed = ids
    .split(/[\s,،]+/)
    .map((x) => x.trim())
    .filter(Boolean);
  const invalid = parsed.filter((x) => !/^\d{3,20}$/.test(x));
  const save = useAction<void>({
    fn: () => api.patch(base, { settings: { referrer_pool: parsed } }),
    invalidate: [keys.acc(account.slug), keys.accounts],
    success: "استخر معرف‌ها ذخیره شد.",
  });
  const check = useAction<void>({
    fn: () => api.post(`${base}/tracking/pool/check`),
    invalidate: [[...keys.acc(account.slug), "tracking"]],
    success: "معرف‌ها در ربات بررسی شدند.",
  });
  return (
    <Card id="tracking">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <LinkIcon className="size-4" /> ردیابی فروش هر آگهی
        </CardTitle>
        <CardDescription>
          ربات فقط وقتی کاربر را به یک منبع نسبت می‌دهد که پارامتر start شناسه‌ی عددی یک کاربر موجود در ربات باشد. شناسه‌ی
          مشتری‌های واقعی را اینجا نگذارید؛ ممکن است کمیسیون و پیام دعوت بگیرند.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Field
          label="شناسه‌ی عددی حساب‌های معرفِ خودتان"
          htmlFor="pool-ids"
          hint="هر خط یک شناسه. این حساب‌ها را خودتان در ربات ساخته‌اید (مثلاً حساب‌های جانبی)."
          error={invalid.length ? `شناسه‌ی نامعتبر: ${invalid.join("، ")}` : undefined}
        >
          <Textarea id="pool-ids" dir="ltr" rows={4} className="text-left" value={ids} onChange={(e) => setIds(e.target.value)} />
        </Field>
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => save.mutate()} disabled={save.isPending || invalid.length > 0 || parsed.join(",") === snapshot}>
            <SaveIcon /> ذخیره‌ی استخر
          </Button>
          <Button
            variant="outline"
            onClick={() => check.mutate()}
            disabled={check.isPending || !account.bot.configured || !account.settings.referrer_pool.length}
          >
            <RefreshCwIcon className={check.isPending ? "animate-spin" : ""} /> بررسی معرف‌ها در ربات
          </Button>
        </div>
        {!!tracking.data?.pool.length && (
          <div className="grid gap-1.5 text-sm" data-testid="pool">
            {tracking.data.pool.map((p) => (
              <div key={p.referrer_id} className="flex flex-wrap items-center gap-2 rounded-md border px-3 py-2">
                <span className="ltr font-medium">{p.referrer_id}</span>
                {p.check ? (
                  p.check.exists ? (
                    <Badge variant="success">در ربات هست</Badge>
                  ) : (
                    <Badge variant="destructive">پیدا نشد</Badge>
                  )
                ) : (
                  <Badge variant="secondary">بررسی نشده</Badge>
                )}
                {p.check?.invited != null && (
                  <Badge variant={p.check.invited > 0 ? "warning" : "outline"} className="num">
                    {num(p.check.invited)} دعوت قبلی
                  </Badge>
                )}
                <span className="text-xs text-muted-foreground">
                  {p.free ? "آزاد" : `${num(p.ads)} آگهی`}
                  {p.tag_name && ` · برچسب «${p.tag_name}»`}
                </span>
                {!!p.check?.invited && (
                  <span className="w-full text-xs text-warning">این معرف از قبل دعوت دارد؛ آمار آگهی‌هایش با دعوت‌های قبلی مخلوط می‌شود.</span>
                )}
                {p.check?.error && !p.check.exists && <span className="w-full text-xs text-destructive">{p.check.error}</span>}
              </div>
            ))}
          </div>
        )}
        <RandomTrackingTest account={account} />
      </CardContent>
    </Card>
  );
}

const RANDOM_RESULT: Record<string, { label: string; variant: "success" | "warning" | "secondary" }> = {
  works: { label: "کار می‌کند: ربات کاربران کد رندوم را ثبت می‌کند", variant: "success" },
  ignored: { label: "ربات کد ناشناخته را نادیده می‌گیرد", variant: "warning" },
  unknown: { label: "آزمایش نشده / هنوز کسی لینک را استارت نکرده", variant: "secondary" },
};

/** Does the bot record users who start it with an unknown numeric code? (decides whether random links track sales) */
function RandomTrackingTest({ account }: { account: Account }) {
  const tracking = useTracking(account.slug);
  const base = accountApi(account.slug);
  const inv = [keys.acc(account.slug), keys.accounts];
  const start = useAction<void>({
    fn: () => api.post(`${base}/tracking/random/test`),
    invalidate: inv,
    success: "کد آزمایشی ساخته شد.",
  });
  const check = useAction<void, { test: { result: string } }>({
    fn: () => api.post(`${base}/tracking/random/check`),
    invalidate: inv,
    success: (r) => RANDOM_RESULT[r.test.result]?.label ?? "بررسی شد.",
  });
  const result = tracking.data?.random.result ?? account.settings.random_tracking;
  const test = tracking.data?.random.test;
  return (
    <div className="space-y-3 rounded-lg border p-3" id="random-test" data-testid="random-test">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm font-medium">آزمایش ردیابی کد رندوم</div>
        <Badge variant={RANDOM_RESULT[result]?.variant ?? "secondary"}>{RANDOM_RESULT[result]?.label ?? result}</Badge>
      </div>
      <p className="text-xs leading-relaxed text-muted-foreground">
        گزینه‌ی «لینک رندوم اختصاصی برای هر آگهی» برای هر آگهی یک کد عددی تصادفی می‌سازد. ربات فعلاً فقط وقتی کاربر را به یک منبع
        نسبت می‌دهد که start شناسه‌ی یک کاربر موجود باشد و معلوم نیست کد ناشناخته را ذخیره کند. این آزمایش آن را مشخص می‌کند:
      </p>
      <ol className="list-decimal space-y-1 ps-5 text-xs leading-relaxed text-muted-foreground">
        <li>«ساخت کد آزمایشی» را بزنید.</li>
        <li>با یک حساب تلگرامی که تا حالا این ربات را استارت نکرده، لینک زیر را باز و ربات را استارت کنید.</li>
        <li>«بررسی نتیجه» را بزنید.</li>
      </ol>
      {test?.link && (
        <div className="flex items-center gap-1 rounded-md bg-muted/50 px-2 py-1.5 text-xs">
          <span className="ltr min-w-0 flex-1 truncate" data-testid="random-test-link">
            {test.link}
          </span>
          <CopyButton value={test.link} />
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" onClick={() => start.mutate()} disabled={start.isPending || !account.bot.configured}>
          ساخت کد آزمایشی
        </Button>
        <Button size="sm" onClick={() => check.mutate()} disabled={check.isPending || !test?.code}>
          بررسی نتیجه
        </Button>
      </div>
      {result !== "works" && tracking.data?.campaign_request && (
        <div className="flex items-center gap-1 text-xs text-muted-foreground">
          اگر ربات کد ناشناخته را ذخیره نمی‌کند، این متن را برای توسعه‌دهنده‌ی ربات بفرستید
          <CopyButton value={tracking.data.campaign_request} label="کپی متن درخواست" />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- general

function GeneralCard({ account }: { account: Account }) {
  const [name, setName] = useState(account.name);
  const [color, setColor] = useState(account.color);
  const base = accountApi(account.slug);
  const inv = [keys.acc(account.slug), keys.accounts, keys.overview];
  const save = useAction<Record<string, unknown>>({
    fn: (body) => api.patch(base, body),
    invalidate: inv,
    success: "ذخیره شد.",
  });
  return (
    <Card id="general">
      <CardHeader>
        <CardTitle className="text-base">عمومی</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-5 md:grid-cols-2">
        <Field label="نام نمایشی" htmlFor="acc-name" hint={<span className="ltr">/accounts/{account.slug}</span>}>
          <Input id="acc-name" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="رنگ">
          <div className="flex flex-wrap gap-2">
            {COLORS.map((c) => (
              <button
                key={c.id}
                type="button"
                title={c.label}
                aria-label={c.label}
                aria-pressed={color === c.id}
                data-accent={c.id}
                onClick={() => setColor(c.id)}
                className={cn(
                  "flex size-8 items-center justify-center rounded-full ring-offset-2 ring-offset-background",
                  color === c.id && "ring-2 ring-ring",
                )}
                style={{ background: "var(--acc)" }}
              >
                {color === c.id && <CheckIcon className="size-4 text-white" />}
              </button>
            ))}
          </div>
        </Field>
        <div className="flex items-center justify-between gap-4 rounded-lg border p-3 md:col-span-2">
          <div>
            <div className="text-sm font-medium">اجرای اقدام‌ها</div>
            <div className="text-xs text-muted-foreground">
              اگر خاموش باشد، پیشنهادها ساخته می‌شوند ولی هیچ شارژ یا برداشتی اجرا نمی‌شود.
            </div>
          </div>
          <Switch
            checked={!account.automation_paused}
            onCheckedChange={(on) => save.mutate({ automation_paused: !on })}
            aria-label="اجرای اقدام‌ها"
          />
        </div>
      </CardContent>
      <CardFooter className="justify-end">
        <Button
          disabled={save.isPending || !name.trim() || (name === account.name && color === account.color)}
          onClick={() => save.mutate({ name: name.trim(), color })}
        >
          <SaveIcon /> ذخیره
        </Button>
      </CardFooter>
    </Card>
  );
}

// ---------------------------------------------------------------- danger zone

function DangerCard({ account }: { account: Account }) {
  const base = accountApi(account.slug);
  const nav = useNavigate();
  const qc = useQueryClient();
  const [killText, setKillText] = useState("");
  const [delText, setDelText] = useState("");
  const [busy, setBusy] = useState(false);
  const kill = useAction<void, { paused_ads: number }>({
    fn: () => api.post(`${base}/kill`, { confirm: killText }),
    invalidate: [keys.acc(account.slug), keys.accounts],
    success: (r) => `${num(r.paused_ads)} آگهی متوقف شد و اجرای خودکار خاموش شد.`,
    onSuccess: () => setKillText(""),
  });

  const remove = async () => {
    setBusy(true);
    try {
      await api.del(base);
      qc.removeQueries({ queryKey: keys.acc(account.slug) });
      await qc.invalidateQueries({ queryKey: keys.accounts });
      await qc.invalidateQueries({ queryKey: keys.overview });
      toast.success("حساب حذف شد.");
      nav("/");
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card id="danger" className="border-destructive/40">
      <CardHeader>
        <CardTitle className="text-base text-destructive">ناحیه‌ی خطر</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <div className="text-sm font-medium">توقف اضطراری</div>
            <div className="text-xs text-muted-foreground">همه‌ی آگهی‌های فعال این حساب متوقف و اجرای خودکار خاموش می‌شود.</div>
          </div>
          <Dialog>
            <DialogTrigger asChild>
              <Button variant="destructive">
                <OctagonXIcon /> توقف همه‌ی آگهی‌ها
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>توقف همه‌ی آگهی‌های {account.name}</DialogTitle>
                <DialogDescription>برای تأیید، کلمه‌ی «توقف» را بنویسید.</DialogDescription>
              </DialogHeader>
              <Input value={killText} onChange={(e) => setKillText(e.target.value)} placeholder="توقف" />
              <DialogFooter>
                <Button variant="destructive" disabled={killText.trim() !== "توقف" || kill.isPending} onClick={() => kill.mutate()}>
                  توقف همه
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </div>
        <Separator />
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <div className="text-sm font-medium">حذف حساب از پنل</div>
            <div className="text-xs text-muted-foreground">
              فقط داده‌های این پنل پاک می‌شود؛ آگهی‌ها در تلگرام دست نمی‌خورند.
            </div>
          </div>
          <Dialog>
            <DialogTrigger asChild>
              <Button variant="outline" className="border-destructive/50 text-destructive">
                <Trash2Icon /> حذف حساب
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>حذف «{account.name}»</DialogTitle>
                <DialogDescription>
                  همه‌ی آمار، برچسب‌ها، متن‌ها و تحلیل‌های این حساب پاک می‌شوند. برای تأیید، شناسه‌ی{" "}
                  <code className="ltr">{account.slug}</code> را بنویسید.
                </DialogDescription>
              </DialogHeader>
              <Input dir="ltr" value={delText} onChange={(e) => setDelText(e.target.value)} placeholder={account.slug} />
              <DialogFooter>
                <Button variant="destructive" disabled={delText.trim() !== account.slug || busy} onClick={remove}>
                  حذف برای همیشه
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </div>
        {account.automation_paused && (
          <Badge variant="warning">اجرای خودکار این حساب الان خاموش است</Badge>
        )}
      </CardContent>
    </Card>
  );
}

function Info({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="num truncate font-medium">{value}</div>
    </div>
  );
}
