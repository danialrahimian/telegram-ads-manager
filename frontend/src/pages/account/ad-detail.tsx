import {
  ArchiveIcon,
  ArrowRightIcon,
  ExternalLinkIcon,
  LayersIcon,
  PencilIcon,
  RefreshCwIcon,
  TagIcon,
  WalletIcon,
} from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router";
import { AdActions } from "@/components/ad-actions";
import { BulkDialog } from "@/components/bulk-dialog";
import { AdStatusBadge, placementLabel } from "@/components/ad-status";
import { CopyButton } from "@/components/copy-button";
import { EditProbe } from "@/components/edit-probe";
import { Field } from "@/components/field";
import { useAccountCtx } from "@/components/layout/account-layout";
import { QueryError } from "@/components/query-error";
import { SeriesChart } from "@/components/series-chart";
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { budgetless } from "@/lib/bulk";
import { date, dateTime, dayLabel, money, num, pct, toman } from "@/lib/format";
import { accountApi, keys, useAction, useAd, useTags, useTexts } from "@/lib/queries";
import type { AdRow } from "@/lib/types";
import { cn } from "@/lib/utils";

const JOB_STATUS: Record<string, { label: string; variant: "success" | "warning" | "destructive" | "secondary" | "info" }> = {
  queued: { label: "در صف", variant: "info" },
  running: { label: "در حال اجرا", variant: "info" },
  done: { label: "انجام شد", variant: "success" },
  failed: { label: "ناموفق", variant: "destructive" },
  cancelled: { label: "لغو شد", variant: "secondary" },
};
const JOB_KIND: Record<string, string> = {
  topup: "شارژ",
  withdraw: "برداشت",
  pause: "توقف",
  resume: "فعال‌سازی",
  create_ad: "ساخت آگهی جایگزین",
  edit_ad: "ویرایش آگهی",
  delete_ad: "حذف آگهی",
};
const BUDGET_KIND: Record<string, string> = {
  funded: "شارژ از صفر",
  empty: "بودجه تمام شد",
  topup: "شارژ",
  withdraw: "برداشت",
};
const ACTOR: Record<string, string> = { app: "پنل", site: "سایت تلگرام", sync: "همگام‌سازی" };

export function JobStatus({ status }: { status: string }) {
  const s = JOB_STATUS[status] ?? { label: status, variant: "secondary" as const };
  return <Badge variant={s.variant}>{s.label}</Badge>;
}

export default function AdDetailPage() {
  const account = useAccountCtx();
  const adId = Number(useParams().adId);
  const { data, isLoading, error, refetch } = useAd(account.slug, adId);
  const [editing, setEditing] = useState(false);
  const [replacing, setReplacing] = useState(false);
  const cur = account.telegram.currency;

  if (isLoading) return <Skeleton className="h-96" />;
  if (error || !data) return <QueryError error={error} retry={() => refetch()} />;
  const ad = data.ad;

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 space-y-1.5">
          <Button asChild variant="ghost" size="sm" className="text-muted-foreground">
            <Link to=".." relative="path">
              <ArrowRightIcon /> آگهی‌ها
            </Link>
          </Button>
          <h1 className="flex flex-wrap items-center gap-2 text-xl font-bold">
            <span className="truncate">{ad.target_label || ad.title || `آگهی ${ad.ad_id}`}</span>
            <AdStatusBadge status={ad.status} paused={ad.is_paused} />
            {budgetless(ad) && (
              <Badge variant="warning">
                <WalletIcon /> {budgetless(ad)}
              </Badge>
            )}
            {ad.archived && (
              <Badge variant="secondary">
                <ArchiveIcon /> بایگانی‌شده
              </Badge>
            )}
          </h1>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <span className="num">#{ad.ad_id}</span>
            <span>{placementLabel(ad.placement)}</span>
            <span>CPM: <span className="num">{money(ad.cpm, cur)}</span></span>
            <span>ساخت: {date(ad.created_date)}</span>
            {ad.age_days != null && <span className="num">{num(ad.age_days)} روز</span>}
          </div>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
            <PencilIcon /> ویرایش
          </Button>
          <AdActions account={account} ad={ad} />
        </div>
      </div>

      {data.bulk.length > 0 && (
        <Alert variant="info">
          <LayersIcon />
          <AlertTitle>در عملیات گروهی</AlertTitle>
          <AlertDescription>
            {data.bulk.map((b) => (
              <p key={b.item_id}>
                <Link className="underline" to={`/accounts/${account.slug}/bulk/${b.op_id}`}>
                  عملیات #{b.op_id}
                </Link>
                {b.old_ad_id === ad.ad_id && b.new_ad_id ? ` — جایگزین: #${b.new_ad_id}` : ""}
                {b.new_ad_id === ad.ad_id ? ` — جایگزین آگهی #${b.old_ad_id}` : ""}
              </p>
            ))}
          </AlertDescription>
        </Alert>
      )}

      {ad.decline_reason && (
        <Alert variant="destructive">
          <AlertTitle>دلیل رد شدن</AlertTitle>
          <AlertDescription>{ad.decline_reason}</AlertDescription>
        </Alert>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="کل خرج" value={money(ad.spent, cur)} hint={`۷ روز: ${money(ad.spent_7, cur)}`} />
        <StatCard label="استارت" value={num(ad.starts)} hint={`۷ روز: ${num(ad.starts_7)}`} />
        <StatCard
          label="هزینه هر استارت"
          value={money(ad.cps, cur)}
          tone={ad.cps != null && ad.cps > account.settings.stop_cost_per_start && ad.has_stats ? "bad" : undefined}
          hint={`۷ روز: ${money(ad.cps_7, cur)}`}
        />
        <StatCard label="بودجه‌ی باقی‌مانده" value={money(ad.remaining, cur)} />
        <StatCard label="نمایش" value={num(ad.views)} hint={`CTR ${pct(ad.ctr)}`} />
        <StatCard label="کلیک" value={num(ad.clicks)} hint={`${pct(ad.start_rate)} استارت از کلیک`} />
        <StatCard
          label="خریدار (از رفرال)"
          value={ad.buyers == null ? "—" : num(ad.buyers)}
          hint={ad.tag_shared ? "برچسب مشترک بین چند آگهی" : ad.tag_id ? undefined : "برچسب رفرال ندارد"}
        />
        <StatCard
          label="هزینه هر مشتری"
          value={money(ad.cpp, cur)}
          tone={ad.cpp == null ? undefined : ad.cpp <= account.settings.target_cost_per_customer ? "good" : "bad"}
          hint={ad.revenue != null ? `فروش: ${toman(ad.revenue)}` : undefined}
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">روند روزانه</CardTitle>
        </CardHeader>
        <CardContent>
          {data.daily.length ? (
            <SeriesChart
              data={data.daily}
              x="day"
              xFormat={dayLabel}
              series={[
                { key: "spent", label: "خرج", color: "var(--chart-1)", type: "bar", format: (v) => money(v) },
                { key: "starts", label: "استارت", color: "var(--chart-2)", type: "line", axis: "secondary" },
              ]}
            />
          ) : (
            <p className="text-sm text-muted-foreground">آمار روزانه هنوز خوانده نشده است.</p>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">متن و لینک</CardTitle>
            <CardAction>
              <Button size="icon-sm" variant="ghost" onClick={() => setEditing(true)} aria-label="ویرایش">
                <PencilIcon />
              </Button>
            </CardAction>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <p className="rounded-lg bg-muted/50 p-3 leading-7 whitespace-pre-wrap wrap-anywhere">{ad.text}</p>
            <div className="flex items-center gap-1 rounded-lg border px-2 py-1">
              <span className="ltr min-w-0 flex-1 truncate text-xs">{ad.promote_url}</span>
              <CopyButton value={ad.promote_url} />
              <Button asChild size="icon-sm" variant="ghost" aria-label="باز کردن">
                <a href={ad.promote_url} target="_blank" rel="noreferrer noopener">
                  <ExternalLinkIcon />
                </a>
              </Button>
            </div>
            <div className="flex flex-wrap gap-2">
              {ad.tag_name ? (
                <Badge variant="info" className="max-w-full">
                  <TagIcon /> <span className="truncate">{ad.tag_name}</span>
                </Badge>
              ) : (
                <Badge variant="secondary">بدون برچسب رفرال</Badge>
              )}
              {ad.start_code && <Badge variant="outline" className="ltr">start={ad.start_code}</Badge>}
            </div>
            {ad.note && <p className="text-xs text-muted-foreground">یادداشت: {ad.note}</p>}
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="outline" onClick={() => setReplacing(true)}>
                <RefreshCwIcon /> جایگزینی همین آگهی
              </Button>
              <EditProbe account={account} ad={ad} />
            </div>
            <p className="text-xs text-muted-foreground">
              «جایگزینی» آگهی تازه‌ای با متن یا عکس جدید و همان جایگاه و CPM می‌سازد؛ بعد از تأیید تلگرام، این آگهی متوقف و
              بودجه‌اش منتقل می‌شود. وقتی API ویرایش این آگهی را قبول نمی‌کند، همین راه کار می‌کند.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">کارها و پیشنهادهای این آگهی</CardTitle>
            <CardDescription>آخرین اقدام‌های اجراشده و پیشنهادهای قوانین</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {data.jobs.length === 0 && data.proposals.length === 0 && (
              <p className="text-muted-foreground">هنوز کاری برای این آگهی ثبت نشده است.</p>
            )}
            {data.jobs.map((j) => (
              <div key={`j${j.id}`} className="flex items-center justify-between gap-2 rounded-lg border px-3 py-2">
                <div className="min-w-0">
                  <div className="font-medium">
                    {JOB_KIND[j.kind] ?? j.kind}
                    {j.amount ? <span className="num"> · {money(j.amount, cur)}</span> : null}
                  </div>
                  <div className="truncate text-xs text-muted-foreground">
                    {dateTime(j.created_ts)} {j.result && `· ${j.result}`}
                  </div>
                </div>
                <JobStatus status={j.status} />
              </div>
            ))}
            {data.proposals.map((p) => (
              <div key={`p${p.id}`} className="rounded-lg border border-dashed px-3 py-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{p.label}</span>
                  <Badge variant={p.status === "pending" ? "warning" : "secondary"}>
                    {p.status === "pending" ? "در انتظار" : p.status === "rejected" ? "رد شد" : p.status === "expired" ? "منقضی" : "تأیید شد"}
                  </Badge>
                </div>
                <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{p.reason}</p>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">تاریخچه‌ی بودجه</CardTitle>
          <CardDescription>
            قوانین فقط روزهایی را می‌سنجند که آگهی بودجه داشته؛ {num(ad.funded_days_28)} روز از ۲۸ روز اخیرِ دارای بودجه، هر استارت{" "}
            {money(ad.cps_f28, cur)}.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-1.5 text-sm">
          {data.budget_history.length === 0 && (
            <p className="text-muted-foreground">هنوز تغییری در بودجه‌ی این آگهی دیده نشده است.</p>
          )}
          {data.budget_history.map((e, i) => (
            <div key={i} className="flex flex-wrap items-center justify-between gap-2 rounded-md px-2 py-1.5 odd:bg-muted/40">
              <span>
                {BUDGET_KIND[e.kind] ?? e.kind} <span className="text-xs text-muted-foreground">({ACTOR[e.actor] ?? e.actor})</span>
              </span>
              <span className="num text-xs text-muted-foreground">
                {money(e.amount, cur)} → {money(e.remaining, cur)} · {dateTime(e.ts)}
              </span>
            </div>
          ))}
        </CardContent>
      </Card>

      {replacing && (
        <BulkDialog
          account={account}
          kind="content"
          targets={[
            {
              ad_id: ad.ad_id,
              label: ad.target_label || ad.title || `#${ad.ad_id}`,
              text: ad.text,
              placement: ad.placement,
              target_type: ad.target_type,
            },
          ]}
          onClose={() => setReplacing(false)}
        />
      )}
      {editing && <EditAdDialog ad={ad} onClose={() => setEditing(false)} />}
    </div>
  );
}

function EditAdDialog({ ad, onClose }: { ad: AdRow; onClose: () => void }) {
  const account = useAccountCtx();
  const tags = useTags(account.slug);
  const texts = useTexts(account.slug);
  const [text, setText] = useState(ad.text);
  const [tagId, setTagId] = useState(ad.tag_id ? String(ad.tag_id) : "none");
  const [variantId, setVariantId] = useState(ad.variant_id ? String(ad.variant_id) : "none");
  const [note, setNote] = useState(ad.note ?? "");
  const save = useAction<Record<string, unknown>, { message: string }>({
    fn: (body) => api.patch(`${accountApi(account.slug)}/ads/${ad.ad_id}`, body),
    invalidate: [keys.acc(account.slug)],
    onSuccess: onClose,
  });
  const tooLong = text.length > 160;

  const submit = () => {
    const body: Record<string, unknown> = {};
    if (text.trim() !== ad.text) body.text = text.trim();
    const newTag = tagId === "none" ? null : Number(tagId);
    if (newTag !== ad.tag_id) {
      if (newTag === null) body.clear_tag = true;
      else body.tag_id = newTag;
    }
    const newVar = variantId === "none" ? null : Number(variantId);
    if (newVar !== ad.variant_id && newVar !== null) body.variant_id = newVar;
    if (note !== (ad.note ?? "")) body.note = note;
    if (Object.keys(body).length === 0) return onClose();
    save.mutate(body);
  };

  const remote = text.trim() !== ad.text || (tagId === "none" ? null : Number(tagId)) !== ad.tag_id;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>ویرایش آگهی #{ad.ad_id}</DialogTitle>
          <DialogDescription>
            تغییر متن یا برچسب رفرال به تلگرام فرستاده می‌شود و آگهی دوباره بررسی می‌شود. یادداشت فقط اینجا ذخیره می‌شود.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <Field
            label="متن آگهی"
            htmlFor="ad-text"
            error={tooLong ? "متن بیشتر از ۱۶۰ حرف است." : undefined}
            hint={<span className="num">{num(text.length)} / ۱۶۰</span>}
          >
            <Textarea id="ad-text" rows={4} value={text} onChange={(e) => setText(e.target.value)} aria-invalid={tooLong} />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="برچسب رفرال" hint="لینک آگهی به ?start=کد این برچسب تغییر می‌کند">
              <Select value={tagId} onValueChange={setTagId}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">بدون برچسب</SelectItem>
                  {tags.data?.map((t) => (
                    <SelectItem key={t.id} value={String(t.id)}>
                      {t.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field label="متن کتابخانه" hint="برای مقایسه‌ی عملکرد متن‌ها">
              <Select value={variantId} onValueChange={setVariantId}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">—</SelectItem>
                  {texts.data?.map((t) => (
                    <SelectItem key={t.id} value={String(t.id)}>
                      {t.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          </div>
          <Field label="یادداشت" htmlFor="ad-note">
            <Input id="ad-note" value={note} onChange={(e) => setNote(e.target.value)} />
          </Field>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            انصراف
          </Button>
          <Button onClick={submit} disabled={save.isPending || tooLong} className={cn(remote && "bg-warning text-black hover:bg-warning/90")}>
            {remote ? "ذخیره و ارسال برای بررسی" : "ذخیره"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
