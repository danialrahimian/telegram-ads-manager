import {
  DownloadIcon,
  ListChecksIcon,
  MegaphoneIcon,
  PencilIcon,
  PlusIcon,
  SparklesIcon,
  Trash2Icon,
  TypeIcon,
} from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { ConfirmButton } from "@/components/confirm-button";
import { CopyButton } from "@/components/copy-button";
import { EmptyState } from "@/components/empty-state";
import { Field } from "@/components/field";
import { useAccountCtx } from "@/components/layout/account-layout";
import { PageHeader } from "@/components/page-header";
import { QueryError } from "@/components/query-error";
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
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { dateTime, money, num, pct } from "@/lib/format";
import { accountApi, keys, useAction, useTexts } from "@/lib/queries";
import type { TextAnalysis, TextVariant } from "@/lib/types";
import { cn } from "@/lib/utils";

type Editing = { id?: number; name: string; text: string } | null;

export default function TextsPage() {
  const account = useAccountCtx();
  const { data, isLoading, error, refetch } = useTexts(account.slug);
  const [editing, setEditing] = useState<Editing>(null);
  const [filter, setFilter] = useState<"active" | "all">("active");
  const base = accountApi(account.slug);
  const inv = [keys.acc(account.slug)];

  const importAds = useAction<void, { imported: number }>({
    fn: () => api.post(`${base}/texts/import`),
    invalidate: inv,
    success: (r) => (r.imported ? `${num(r.imported)} متن از آگهی‌ها وارد شد.` : "متن تازه‌ای در آگهی‌ها پیدا نشد."),
  });

  const list = (data ?? []).filter((t) => filter === "all" || t.active);

  return (
    <div className="space-y-5">
      <PageHeader
        title="متن‌های آگهی"
        description="کتابخانه‌ی متن‌ها: دستی ویرایش کنید، با Claude تحلیل و بازنویسی کنید و عملکرد هر متن را مقایسه کنید."
        actions={
          <>
            <Button variant="outline" onClick={() => importAds.mutate()} disabled={importAds.isPending}>
              <DownloadIcon /> وارد کردن از آگهی‌ها
            </Button>
            <Button onClick={() => setEditing({ name: "", text: "" })}>
              <PlusIcon /> متن جدید
            </Button>
          </>
        }
      />
      <Tabs value={filter} onValueChange={(v) => setFilter(v as "active" | "all")}>
        <TabsList>
          <TabsTrigger value="active">فعال</TabsTrigger>
          <TabsTrigger value="all">همه (با بایگانی)</TabsTrigger>
        </TabsList>
      </Tabs>
      {error && <QueryError error={error} retry={() => refetch()} />}
      {isLoading && <Skeleton className="h-64" />}
      {!isLoading && list.length === 0 && (
        <EmptyState
          icon={TypeIcon}
          title="متنی در کتابخانه نیست"
          description="متن تازه بنویسید یا متن آگهی‌های فعلی را وارد کنید تا با Claude تحلیل شوند."
        />
      )}
      <div className="grid gap-4 xl:grid-cols-2 [&>*]:min-w-0">
        {list.map((t) => (
          <TextCard key={t.id} t={t} onEdit={(e) => setEditing(e)} />
        ))}
      </div>
      {editing && <TextDialog value={editing} onClose={() => setEditing(null)} />}
    </div>
  );
}

function TextCard({ t, onEdit }: { t: TextVariant; onEdit: (e: Editing) => void }) {
  const account = useAccountCtx();
  const base = accountApi(account.slug);
  const inv = [keys.acc(account.slug)];
  const [goal, setGoal] = useState("");
  const [showGoal, setShowGoal] = useState(false);
  const p = t.performance;
  const cur = account.telegram.currency;

  const toggle = useAction<boolean>({
    fn: (active) => api.patch(`${base}/texts/${t.id}`, { active }),
    invalidate: inv,
    success: (_, a) => (a ? "متن فعال شد." : "متن بایگانی شد."),
  });
  const del = useAction<void>({ fn: () => api.del(`${base}/texts/${t.id}`), invalidate: inv, success: "متن حذف شد." });
  const analyze = useAction<void, TextAnalysis>({
    fn: () => api.post(`${base}/texts/${t.id}/analyze`, { goal }),
    invalidate: inv,
    success: "تحلیل Claude آماده شد.",
    onSuccess: () => setShowGoal(false),
  });

  return (
    <Card className={cn(!t.active && "opacity-70")}>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          {t.name}
          {t.source === "claude" && (
            <Badge variant="info">
              <SparklesIcon /> Claude
            </Badge>
          )}
          {t.source === "imported" && <Badge variant="secondary">از آگهی</Badge>}
          {!t.active && <Badge variant="outline">بایگانی</Badge>}
        </CardTitle>
        <CardDescription className="num">
          {num(t.length)} حرف {t.length > 160 && <span className="text-destructive">— بلندتر از حد مجاز</span>}
        </CardDescription>
        <CardAction className="flex items-center gap-1">
          <Switch
            checked={t.active}
            onCheckedChange={(v) => toggle.mutate(v)}
            aria-label="فعال"
            title={t.active ? "بایگانی" : "فعال‌سازی"}
          />
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="relative rounded-lg bg-muted/50 p-3 pe-10 text-sm leading-7 whitespace-pre-wrap wrap-anywhere">
          {t.text}
          <div className="absolute end-1 top-1">
            <CopyButton value={t.text} />
          </div>
        </div>

        {(p.ads ?? 0) > 0 ? (
          <div className="grid grid-cols-3 gap-2 text-center text-xs sm:grid-cols-5">
            <Perf label="آگهی" value={`${num(p.ads)}${p.running ? ` (${num(p.running)} فعال)` : ""}`} />
            <Perf label="CTR" value={pct(p.ctr)} />
            <Perf label="استارت از کلیک" value={pct(p.start_rate)} />
            <Perf label="هر استارت" value={money(p.cps, cur)} />
            <Perf label="هر مشتری" value={money(p.cpp, cur)} />
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">هنوز آگهی‌ای با این متن اجرا نشده است.</p>
        )}

        {t.analysis && <AnalysisView a={t.analysis} at={t.analyzed_at} onUse={(text) => onEdit({ name: `${t.name} (بازنویسی)`, text })} />}

        {showGoal && (
          <Field label="هدف یا توضیح برای Claude (اختیاری)" htmlFor={`goal-${t.id}`}>
            <Input
              id={`goal-${t.id}`}
              placeholder="مثلاً: تمرکز روی تست رایگان و سرعت"
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
            />
          </Field>
        )}

        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            onClick={() => (showGoal ? analyze.mutate() : setShowGoal(true))}
            disabled={analyze.isPending}
          >
            <SparklesIcon className={analyze.isPending ? "animate-pulse" : ""} />
            {analyze.isPending ? "Claude در حال تحلیل…" : showGoal ? "شروع تحلیل" : t.analysis ? "تحلیل دوباره" : "تحلیل با Claude"}
          </Button>
          <Button size="sm" variant="outline" onClick={() => onEdit({ id: t.id, name: t.name, text: t.text })}>
            <PencilIcon /> ویرایش
          </Button>
          {(p.ads ?? 0) > 0 && (
            <Button size="sm" variant="outline" asChild>
              <Link to={`/accounts/${account.slug}/ads?variant=${t.id}&select=all`}>
                <ListChecksIcon /> آگهی‌های این متن
              </Link>
            </Button>
          )}
          <Button size="sm" variant="outline" asChild>
            <Link to={`/accounts/${account.slug}/ads/new?text_id=${t.id}`}>
              <MegaphoneIcon /> آگهی با این متن
            </Link>
          </Button>
          <ConfirmButton
            size="sm"
            variant="ghost"
            className="text-destructive"
            destructive
            title="حذف این متن؟"
            description="آگهی‌ها تغییری نمی‌کنند، ولی آمار مقایسه‌ای این متن از بین می‌رود. برای نگه داشتن آمار، بایگانی‌اش کنید."
            confirmLabel="حذف"
            onConfirm={() => del.mutateAsync()}
          >
            <Trash2Icon /> حذف
          </ConfirmButton>
        </div>
      </CardContent>
    </Card>
  );
}

function Perf({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border px-1 py-1.5">
      <div className="num truncate font-semibold">{value}</div>
      <div className="truncate text-muted-foreground">{label}</div>
    </div>
  );
}

function AnalysisView({ a, at, onUse }: { a: TextAnalysis; at: string | null; onUse: (text: string) => void }) {
  const score = a.score ?? 0;
  return (
    <div className="space-y-3 rounded-lg border border-info/30 bg-info/5 p-3">
      <div className="flex items-center gap-3">
        <div
          className={cn(
            "flex size-12 shrink-0 items-center justify-center rounded-full border-2 text-sm font-bold num",
            score >= 75 ? "border-success text-success" : score >= 50 ? "border-warning text-warning" : "border-destructive text-destructive",
          )}
        >
          {a.score == null ? "—" : num(a.score)}
        </div>
        <div className="min-w-0 space-y-0.5">
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <SparklesIcon className="size-3.5 text-info" /> تحلیل Claude · {dateTime(at)}
          </div>
          <p className="text-sm leading-relaxed">{a.summary}</p>
        </div>
      </div>
      {a.strengths.length > 0 && (
        <div className="space-y-1">
          <div className="text-xs font-medium text-success">نقاط قوت</div>
          <ul className="list-disc space-y-0.5 ps-5 text-xs leading-relaxed">
            {a.strengths.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </div>
      )}
      {a.issues.length > 0 && (
        <div className="space-y-1">
          <div className="text-xs font-medium text-destructive">مشکل‌ها و راه‌حل</div>
          <ul className="space-y-1.5 text-xs leading-relaxed">
            {a.issues.map((it, i) => (
              <li key={i} className="flex gap-2">
                <Badge
                  variant={it.severity === "high" ? "destructive" : it.severity === "medium" ? "warning" : "secondary"}
                  className="mt-0.5 shrink-0"
                >
                  {it.severity === "high" ? "مهم" : it.severity === "medium" ? "متوسط" : "جزئی"}
                </Badge>
                <span>
                  {it.issue}
                  {it.fix && <span className="text-muted-foreground"> — {it.fix}</span>}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {a.rewrites.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-xs font-medium">بازنویسی‌های پیشنهادی</div>
          {a.rewrites.map((r, i) => (
            <div key={i} className="space-y-1.5 rounded-md border bg-background p-2.5">
              <p className="text-sm leading-7 whitespace-pre-wrap wrap-anywhere">{r.text}</p>
              <div className="flex flex-wrap items-center gap-2 text-xs">
                {r.angle && <Badge variant="outline">{r.angle}</Badge>}
                <span className={cn("num", r.too_long ? "text-destructive" : "text-muted-foreground")}>
                  {num(r.length)} حرف{r.too_long && " — باید کوتاه شود"}
                </span>
                <div className="ms-auto flex gap-1">
                  <CopyButton value={r.text} />
                  <Button size="sm" variant="secondary" className="h-7" onClick={() => onUse(r.text)}>
                    ویرایش و ذخیره
                  </Button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function TextDialog({ value, onClose }: { value: NonNullable<Editing>; onClose: () => void }) {
  const account = useAccountCtx();
  const base = accountApi(account.slug);
  const [name, setName] = useState(value.name);
  const [text, setText] = useState(value.text);
  const save = useAction<void, TextVariant>({
    fn: () =>
      value.id
        ? api.patch(`${base}/texts/${value.id}`, { name: name.trim(), text: text.trim() })
        : api.post(`${base}/texts`, { name: name.trim(), text: text.trim() }),
    invalidate: [keys.acc(account.slug)],
    success: (r) => (r.versioned ? "چون آگهی‌ها از متن قبلی استفاده کرده‌اند، نسخه‌ی تازه ساخته شد." : "ذخیره شد."),
    onSuccess: onClose,
  });
  const tooLong = text.trim().length > 160;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{value.id ? "ویرایش متن" : "متن جدید"}</DialogTitle>
          <DialogDescription>متن آگهی تلگرام حداکثر ۱۶۰ حرف است و نباید جای‌خالی مثل [نام] داشته باشد.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <Field label="نام" htmlFor="text-name">
            <Input id="text-name" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field
            label="متن"
            htmlFor="text-body"
            error={tooLong ? "بلندتر از ۱۶۰ حرف" : undefined}
            hint={<span className="num">{num(text.trim().length)} / ۱۶۰</span>}
          >
            <Textarea id="text-body" rows={5} value={text} onChange={(e) => setText(e.target.value)} aria-invalid={tooLong} />
          </Field>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            انصراف
          </Button>
          <Button onClick={() => save.mutate()} disabled={save.isPending || !name.trim() || !text.trim() || tooLong}>
            ذخیره
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
