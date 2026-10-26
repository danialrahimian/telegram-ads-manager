import { InfoIcon, LinkIcon, PencilIcon, PlusIcon, SearchCheckIcon, TagIcon, Trash2Icon } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { ConfirmButton } from "@/components/confirm-button";
import { CopyButton } from "@/components/copy-button";
import { EmptyState } from "@/components/empty-state";
import { Field } from "@/components/field";
import { useAccountCtx } from "@/components/layout/account-layout";
import { PageHeader } from "@/components/page-header";
import { QueryError } from "@/components/query-error";
import { StatCard } from "@/components/stat-card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useNow } from "@/hooks/use-now";
import { api, errorMessage } from "@/lib/api";
import { ago, currencyLabel, money, num, pct, toman } from "@/lib/format";
import { accountApi, keys, useAction, useSales } from "@/lib/queries";
import type { Tag } from "@/lib/types";
import { cn } from "@/lib/utils";

type EditTag = Partial<Tag> | null;

export default function SalesPage() {
  const account = useAccountCtx();
  const [days, setDays] = useState(30);
  const { data, isLoading, error, refetch } = useSales(account.slug, days);
  const [editing, setEditing] = useState<EditTag>(null);
  const now = useNow(10_000);
  const cur = account.telegram.currency;
  const base = accountApi(account.slug);
  const tags = data?.tags ?? [];
  const purchaseTypes = new Set(account.settings.purchase_types);
  const totals = (data?.overview.by_type ?? []).reduce(
    (acc, r) => (purchaseTypes.has(r.type) ? { count: acc.count + r.count, amount: acc.amount + r.amount } : acc),
    { count: 0, amount: 0 },
  );

  const del = useAction<number>({
    fn: (id) => api.del(`${base}/tags/${id}`),
    invalidate: [keys.acc(account.slug)],
    success: "برچسب حذف شد.",
  });

  if (!account.bot.configured) {
    return (
      <div className="space-y-5">
        <PageHeader title="فروش و رفرال" />
        <EmptyState
          icon={TagIcon}
          title="ربات فروش وصل نیست"
          description="برای دیدن فروش و ردیابی هر آگهی، آدرس و کلید API ربات این حساب را در صفحه‌ی اتصال وارد کنید."
          action={
            <Button asChild>
              <Link to={`/accounts/${account.slug}/settings`}>اتصال ربات</Link>
            </Button>
          }
        />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="فروش و رفرال"
        description="هر برچسب، لینک دعوت یک حساب معرف در ربات است. آگهی‌هایی که با این لینک ساخته شوند، کاربر و خریدشان جدا شمرده می‌شود."
        actions={
          <Button onClick={() => setEditing({})}>
            <PlusIcon /> برچسب جدید
          </Button>
        }
      />

      <Alert variant="info">
        <InfoIcon />
        <AlertTitle>ردیابی فروش هر آگهی چطور کار می‌کند؟</AlertTitle>
        <AlertDescription>
          <p>
            ۱) در ربات برای هر گروه آگهی یک حساب معرف جدا بسازید. ۲) شناسه‌ی عددی آن حساب را اینجا به‌عنوان برچسب وارد کنید.
            ۳) موقع ساخت یا ویرایش آگهی، برچسب را انتخاب کنید تا لینک آگهی پسوند <span className="ltr">?start=…</span> با کد همان برچسب را بگیرد. ۴)
            کاربرانی که آن معرف دعوت کرده و خریدهایشان خودکار از ربات خوانده می‌شوند.
          </p>
        </AlertDescription>
      </Alert>

      {error && <QueryError error={error} retry={() => refetch()} />}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold">فروش ربات</h2>
        <Tabs value={String(days)} onValueChange={(v) => setDays(Number(v))}>
          <TabsList>
            <TabsTrigger value="7">۷</TabsTrigger>
            <TabsTrigger value="30">۳۰</TabsTrigger>
            <TabsTrigger value="90">۹۰ روز</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>
      {!!data?.overview.untagged_ads && (
        <Alert variant="warning">
          <LinkIcon />
          <AlertTitle>{num(data.overview.untagged_ads)} آگهی برچسب رفرال ندارد</AlertTitle>
          <AlertDescription>
            <p>
              با یک عملیات گروهی به هر کدام یک معرف آزاد از استخر معرف‌ها (تنظیمات حساب) داده می‌شود و لینکش عوض می‌شود. قبل از
              اجرا پیش‌نمایش می‌بینید.
            </p>
            <Button asChild size="sm" className="mt-2">
              <Link to={`/accounts/${account.slug}/ads?referral=without&select=all&bulk=referral`}>ساخت برچسب برای همه</Link>
            </Button>
          </AlertDescription>
        </Alert>
      )}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <StatCard label="تعداد خرید" value={num(totals.count)} loading={isLoading} hint={`${num(days)} روز اخیر`} />
        <StatCard label="مبلغ خرید" value={toman(totals.amount)} loading={isLoading} />
        <StatCard
          label="کاربر از رفرال آگهی‌ها"
          value={num(tags.reduce((a, t) => a + t.users, 0))}
          loading={isLoading}
          hint="همه‌ی زمان‌ها"
        />
        <StatCard
          label="آگهی‌های بدون برچسب"
          value={num(data?.overview.untagged_ads)}
          loading={isLoading}
          tone={data?.overview.untagged_ads ? "bad" : undefined}
          hint="فروششان جدا شمرده نمی‌شود"
        />
        <StatCard
          label="تراکنش ذخیره‌شده"
          value={num(data?.overview.transactions_stored)}
          loading={isLoading}
          hint={`آخرین: ${ago(data?.overview.last_tx_ts, now)}`}
        />
      </div>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Card className="min-w-0">
          <CardHeader>
            <CardTitle className="text-base">برچسب‌های رفرال</CardTitle>
            <CardDescription>عملکرد هر لینک دعوت: کاربر، خریدار و هزینه‌ی هر مشتری</CardDescription>
          </CardHeader>
          <CardContent>
            {isLoading && <Skeleton className="h-40" />}
            {!isLoading && tags.length === 0 && (
              <EmptyState
                icon={TagIcon}
                title="هنوز برچسبی نساخته‌اید"
                action={
                  <Button size="sm" onClick={() => setEditing({})}>
                    <PlusIcon /> اولین برچسب
                  </Button>
                }
              />
            )}
            {tags.length > 0 && (
              <>
                <div className="hidden md:block">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>برچسب</TableHead>
                        <TableHead className="text-end">آگهی</TableHead>
                        <TableHead className="text-end">کاربر</TableHead>
                        <TableHead className="text-end">خریدار</TableHead>
                        <TableHead className="text-end">فروش</TableHead>
                        <TableHead className="text-end">خرج آگهی</TableHead>
                        <TableHead className="text-end">هر مشتری</TableHead>
                        <TableHead />
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {tags.map((t) => (
                        <TableRow key={t.id}>
                          <TableCell className="max-w-[16rem] whitespace-normal">
                            <div className="font-medium">{t.name}</div>
                            <div className="flex items-center gap-1 text-xs text-muted-foreground">
                              <span className="ltr truncate">{t.link || `start=${t.start_code}`}</span>
                              {t.link && <CopyButton value={t.link} />}
                            </div>
                            {t.last_error && <div className="text-xs text-destructive">{t.last_error}</div>}
                          </TableCell>
                          <TableCell className="num text-end">{num(t.ads.length)}</TableCell>
                          <TableCell className="num text-end">{num(t.users)}</TableCell>
                          <TableCell className="num text-end">
                            {num(t.buyers)} <span className="text-xs text-muted-foreground">({pct(t.buy_rate)})</span>
                          </TableCell>
                          <TableCell className="num text-end">{toman(t.revenue)}</TableCell>
                          <TableCell className="num text-end">{money(t.ad_spend, cur)}</TableCell>
                          <TableCell
                            className={cn(
                              "num text-end font-medium",
                              t.cpp != null && (t.cpp <= account.settings.target_cost_per_customer ? "text-success" : "text-destructive"),
                            )}
                          >
                            {money(t.cpp, cur)}
                          </TableCell>
                          <TableCell>
                            <TagRowActions t={t} onEdit={() => setEditing(t)} onDelete={() => del.mutateAsync(t.id)} />
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
                <div className="grid gap-2 md:hidden">
                  {tags.map((t) => (
                    <div key={t.id} className="space-y-2 rounded-lg border p-3">
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <div className="font-medium">{t.name}</div>
                          <div className="ltr truncate text-xs text-muted-foreground">{t.link || `start=${t.start_code}`}</div>
                        </div>
                        <TagRowActions t={t} onEdit={() => setEditing(t)} onDelete={() => del.mutateAsync(t.id)} />
                      </div>
                      <div className="grid grid-cols-4 gap-1.5 text-center text-xs">
                        <Mini label="کاربر" value={num(t.users)} />
                        <Mini label="خریدار" value={num(t.buyers)} />
                        <Mini label={`خرج ${currencyLabel(cur)}`} value={money(t.ad_spend)} />
                        <Mini label="هر مشتری" value={money(t.cpp)} />
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">تراکنش‌ها بر اساس نوع</CardTitle>
            <CardDescription>
              نوع‌هایی که خرید حساب می‌شوند در تنظیمات حساب قابل تغییرند.
            </CardDescription>
            <CardAction>
              <Button asChild variant="ghost" size="icon-sm" aria-label="تنظیمات">
                <Link to={`/accounts/${account.slug}/settings#bot-settings`}>
                  <PencilIcon />
                </Link>
              </Button>
            </CardAction>
          </CardHeader>
          <CardContent className="space-y-1.5">
            {isLoading && <Skeleton className="h-32" />}
            {data?.overview.by_type.length === 0 && (
              <p className="text-sm text-muted-foreground">در این بازه تراکنشی نیست.</p>
            )}
            {data?.overview.by_type.map((r) => (
              <div key={r.type} className="flex items-center justify-between gap-2 rounded-md px-2 py-1.5 text-sm odd:bg-muted/40">
                <span className="flex items-center gap-1.5">
                  <span className="ltr">{r.type}</span>
                  {purchaseTypes.has(r.type) && <Badge variant="success">خرید</Badge>}
                </span>
                <span className="num text-xs text-muted-foreground">
                  {num(r.count)} · {toman(r.amount)}
                </span>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>

      {editing && <TagDialog value={editing} onClose={() => setEditing(null)} />}
    </div>
  );
}

function Mini({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-muted/50 px-1 py-1">
      <div className="num truncate font-semibold">{value}</div>
      <div className="text-muted-foreground">{label}</div>
    </div>
  );
}

function TagRowActions({ t, onEdit, onDelete }: { t: Tag; onEdit: () => void; onDelete: () => Promise<unknown> }) {
  return (
    <div className="flex justify-end gap-1">
      <Button size="icon-sm" variant="ghost" aria-label="ویرایش" onClick={onEdit}>
        <PencilIcon />
      </Button>
      <ConfirmButton
        size="icon-sm"
        variant="ghost"
        aria-label="حذف"
        destructive
        title={`حذف برچسب «${t.name}»؟`}
        description="آگهی‌ها و تراکنش‌ها حذف نمی‌شوند؛ فقط ارتباطشان با این برچسب برداشته می‌شود."
        confirmLabel="حذف"
        onConfirm={onDelete}
      >
        <Trash2Icon className="text-destructive" />
      </ConfirmButton>
    </div>
  );
}

function TagDialog({ value, onClose }: { value: Partial<Tag>; onClose: () => void }) {
  const account = useAccountCtx();
  const base = accountApi(account.slug);
  const [name, setName] = useState(value.name ?? "");
  const [ref, setRef] = useState(value.referrer_id ?? "");
  const [code, setCode] = useState(value.start_code ?? "");
  const [note, setNote] = useState(value.note ?? "");
  const [lookup, setLookup] = useState<{ ok: boolean; text: string } | null>(null);
  const [checking, setChecking] = useState(false);

  const save = useAction<void>({
    fn: () =>
      value.id
        ? api.patch(`${base}/tags/${value.id}`, { name, referrer_id: ref, start_code: code, note })
        : api.post(`${base}/tags`, { name, referrer_id: ref, start_code: code, note }),
    invalidate: [keys.acc(account.slug)],
    success: value.id ? "برچسب ذخیره شد." : "برچسب ساخته شد.",
    onSuccess: onClose,
  });

  const check = async () => {
    setChecking(true);
    setLookup(null);
    try {
      const r = await api.post<{ ok: boolean; referred?: number | null; warning?: string; error?: string }>(
        `${base}/tags/lookup`,
        { referrer_id: ref.trim() },
      );
      if (!r.ok) setLookup({ ok: false, text: r.error ?? "پیدا نشد" });
      else if (r.referred == null) setLookup({ ok: true, text: r.warning ?? "کاربر پیدا شد" });
      else setLookup({ ok: true, text: `این معرف تا حالا ${num(r.referred)} کاربر دعوت کرده است.` });
    } catch (e) {
      setLookup({ ok: false, text: errorMessage(e) });
    } finally {
      setChecking(false);
    }
  };

  const link = account.bot.username ? `https://t.me/${account.bot.username}?start=${code.trim() || ref.trim()}` : "";
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{value.id ? "ویرایش برچسب" : "برچسب رفرال جدید"}</DialogTitle>
          <DialogDescription>شناسه‌ی عددی تلگرام حساب معرف را از ربات (یا @userinfobot) بگیرید.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <Field label="نام برچسب" htmlFor="tag-name" hint="مثلاً «کانال‌های تکنولوژی — مهر»">
            <Input id="tag-name" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="شناسه‌ی عددی معرف" htmlFor="tag-ref">
            <div className="flex gap-2">
              <Input
                id="tag-ref"
                dir="ltr"
                inputMode="numeric"
                className="text-left"
                value={ref}
                onChange={(e) => setRef(e.target.value.replace(/\D/g, ""))}
              />
              <Button type="button" variant="outline" onClick={check} disabled={!ref || checking}>
                <SearchCheckIcon /> بررسی
              </Button>
            </div>
          </Field>
          {lookup && (
            <p className={cn("text-xs", lookup.ok ? "text-success" : "text-destructive")}>{lookup.text}</p>
          )}
          <Field label="کد start (اختیاری)" htmlFor="tag-code" hint="اگر خالی بماند، همان شناسه‌ی معرف استفاده می‌شود">
            <Input id="tag-code" dir="ltr" className="text-left" value={code} onChange={(e) => setCode(e.target.value)} />
          </Field>
          <Field label="یادداشت" htmlFor="tag-note">
            <Input id="tag-note" value={note} onChange={(e) => setNote(e.target.value)} />
          </Field>
          {link && (
            <div className="flex items-center gap-1 rounded-md bg-muted/50 px-2 py-1.5 text-xs">
              <span className="ltr min-w-0 flex-1 truncate">{link}</span>
              <CopyButton value={link} />
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            انصراف
          </Button>
          <Button onClick={() => save.mutate()} disabled={save.isPending || !name.trim() || !ref.trim()}>
            ذخیره
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
