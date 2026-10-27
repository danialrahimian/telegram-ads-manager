import { ArrowRightIcon, ExternalLinkIcon, ImageIcon, LayersIcon, OctagonXIcon, RotateCcwIcon, ShieldCheckIcon } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router";
import { ConfirmButton } from "@/components/confirm-button";
import { EmptyState } from "@/components/empty-state";
import { EDIT_FORMAT_LABEL } from "@/components/edit-probe";
import { useAccountCtx } from "@/components/layout/account-layout";
import { PageHeader } from "@/components/page-header";
import { QueryError } from "@/components/query-error";
import { TextEditor } from "@/components/text-editor";
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
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { BULK_KIND_LABEL, BULK_MODE_LABEL, ITEM_VARIANT, STEP_LABEL } from "@/lib/bulk";
import { dateTime, faDigits, money, num } from "@/lib/format";
import { accountApi, keys, useAction, useBulkOp, useBulkOps } from "@/lib/queries";
import type { Account, BulkItem, BulkOperation, Capabilities } from "@/lib/types";

const OP_STATUS: Record<BulkOperation["status"], { label: string; variant: "info" | "secondary" | "success" }> = {
  running: { label: "در حال اجرا", variant: "info" },
  stopped: { label: "متوقف شد", variant: "secondary" },
  done: { label: "تمام شد", variant: "success" },
};

export default function BulkPage() {
  const account = useAccountCtx();
  const { opId } = useParams();
  return opId ? <OperationDetail account={account} id={Number(opId)} /> : <OperationList account={account} />;
}

function CapabilityNote({ account, caps }: { account: Account; caps?: Capabilities }) {
  const probe = useAction<void, Capabilities>({
    fn: () => api.post(`${accountApi(account.slug)}/bulk/capabilities/probe`),
    invalidate: [keys.acc(account.slug)],
    success: "پشتیبانی API بدون دست زدن به آگهی‌ها آزمایش شد.",
  });
  const label = (v?: string) => (v === "yes" ? "دارد" : v === "no" ? "ندارد" : "نامعلوم");
  return (
    <Card className="gap-2 py-4">
      <CardContent className="flex flex-col gap-2 text-sm sm:flex-row sm:items-center sm:justify-between">
        <div className="space-y-0.5">
          <div>
            حذف آگهی در API: <Badge variant={caps?.delete_ad === "yes" ? "success" : "secondary"}>{label(caps?.delete_ad)}</Badge>{" "}
            · عوض کردن عکس آگهی موجود: <Badge variant={caps?.edit_photo === "yes" ? "success" : "secondary"}>{label(caps?.edit_photo)}</Badge>
          </div>
          <div>
            ویرایش متن/لینک آگهی موجود:{" "}
            <Badge
              variant={caps?.edit_format === "none" ? "destructive" : caps?.edit_format && caps.edit_format !== "unknown" ? "success" : "secondary"}
              data-testid="edit-format"
            >
              {EDIT_FORMAT_LABEL[caps?.edit_format ?? "unknown"]}
            </Badge>
          </div>
          {caps?.edit_format === "unknown" && (
            <p className="text-xs text-muted-foreground">
              برای آزمایش ویرایش، صفحه‌ی یک آگهی را باز کنید و «آزمایش ویرایش در API» را بزنید.
            </p>
          )}
          <p className="text-xs text-muted-foreground">
            {caps?.delete_ad === "yes"
              ? "آگهی‌های قدیمیِ جایگزین‌شده بعد از برداشت بودجه حذف می‌شوند."
              : "بدون متد حذف، آگهی قدیمی بایگانی می‌شود: متوقف، بدون بودجه و پنهان (فیلتر «بایگانی‌شده» در آگهی‌ها)."}
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={() => probe.mutate()} disabled={probe.isPending}>
          <ShieldCheckIcon /> آزمایش امن
        </Button>
      </CardContent>
    </Card>
  );
}

function OperationList({ account }: { account: Account }) {
  const { data, isLoading, error, refetch } = useBulkOps(account.slug);
  return (
    <div className="space-y-5">
      <PageHeader
        title="عملیات گروهی"
        description="تغییر متن و عکس، لینک رفرال و جایگزینی آگهی‌ها به‌صورت دسته‌ای؛ هر قدم از صف کارها و با تأیید شما اجرا می‌شود."
        actions={
          <Button asChild variant="outline">
            <Link to={`/accounts/${account.slug}/ads`}>انتخاب آگهی‌ها</Link>
          </Button>
        }
      />
      <CapabilityNote account={account} caps={data?.capabilities} />
      {error && <QueryError error={error} retry={() => refetch()} />}
      {isLoading && <Skeleton className="h-40" />}
      {data && data.operations.length === 0 && (
        <EmptyState
          icon={LayersIcon}
          title="هنوز عملیات گروهی‌ای اجرا نشده"
          description="در صفحه‌ی آگهی‌ها چند آگهی را انتخاب کنید و از نوار پایین «متن/عکس» یا «لینک رفرال» را بزنید."
        />
      )}
      <div className="grid gap-3">
        {data?.operations.map((op) => (
          <Link key={op.id} to={String(op.id)} className="block">
            <Card className="gap-3 py-4 transition hover:border-primary/50">
              <CardContent className="space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">
                    #{num(op.id)} {BULK_KIND_LABEL[op.kind]}
                  </span>
                  <Badge variant="outline">{BULK_MODE_LABEL[op.mode]}</Badge>
                  <Badge variant={OP_STATUS[op.status].variant}>{OP_STATUS[op.status].label}</Badge>
                  <span className="ms-auto text-xs text-muted-foreground">{dateTime(op.created_ts)}</span>
                </div>
                <Progress value={op.progress * 100} aria-label="پیشرفت" />
                <Counts op={op} />
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}

function Counts({ op }: { op: BulkOperation }) {
  const order = ["pending", "submitted", "in_review", "approved", "retired", "declined", "failed", "skipped"] as const;
  const labels: Record<string, string> = {
    pending: "در انتظار",
    submitted: "ارسال‌شده",
    in_review: "در بررسی",
    approved: "تأیید",
    retired: "بازنشسته",
    declined: "رد",
    failed: "ناموفق",
    skipped: "کنار گذاشته",
  };
  return (
    <div className="flex flex-wrap gap-1.5 text-xs">
      <span className="num text-muted-foreground">
        {num(op.finished)} از {num(op.total)}
      </span>
      {order
        .filter((k) => op.counts[k])
        .map((k) => (
          <Badge key={k} variant={ITEM_VARIANT[k]} className="num">
            {labels[k]} {num(op.counts[k])}
          </Badge>
        ))}
    </div>
  );
}

function OperationDetail({ account, id }: { account: Account; id: number }) {
  const { data: op, isLoading, error, refetch } = useBulkOp(account.slug, id);
  const base = accountApi(account.slug);
  const [resubmit, setResubmit] = useState<BulkItem | null>(null);
  const stop = useAction<void, { message: string }>({
    fn: () => api.post(`${base}/bulk/${id}/stop`),
    invalidate: [keys.acc(account.slug)],
  });
  if (isLoading) return <Skeleton className="h-96" />;
  if (error || !op) return <QueryError error={error} retry={() => refetch()} />;
  const cur = account.telegram.currency;
  return (
    <div className="space-y-5">
      <div className="space-y-1.5">
        <Button asChild variant="ghost" size="sm" className="text-muted-foreground">
          <Link to={`/accounts/${account.slug}/bulk`}>
            <ArrowRightIcon /> عملیات گروهی
          </Link>
        </Button>
        <h1 className="flex flex-wrap items-center gap-2 text-xl font-bold">
          عملیات #{num(op.id)} — {BULK_KIND_LABEL[op.kind]}
          <Badge variant={OP_STATUS[op.status].variant}>{OP_STATUS[op.status].label}</Badge>
        </h1>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span>{BULK_MODE_LABEL[op.mode]}</span>
          <span>شروع: {dateTime(op.created_ts)}</span>
          {op.mode === "replace" && <span>آگهی قدیمی: {op.params.retire === "delete" ? "حذف" : "بایگانی"}</span>}
          {op.mode === "replace" && (
            <span>بودجه: {op.params.budget_source === "account" ? "از موجودی حساب" : "انتقال از آگهی قدیمی"}</span>
          )}
          {op.params.has_photo && (
            <span className="inline-flex items-center gap-1">
              <ImageIcon className="size-3.5" /> عکس تازه
            </span>
          )}
        </div>
      </div>

      <Card className="gap-3 py-4">
        <CardContent className="space-y-2">
          <Progress value={op.progress * 100} aria-label="پیشرفت" />
          <Counts op={op} />
          {op.note && (
            <p className="text-xs text-muted-foreground" data-testid="bulk-note">
              {faDigits(op.note)}
            </p>
          )}
          <Waves op={op} />
          {op.params.text && (
            <p className="rounded-md bg-muted/50 p-2 text-sm leading-7 wrap-anywhere">{String(op.params.text)}</p>
          )}
        </CardContent>
      </Card>

      {op.status === "running" && (
        <ConfirmButton
          variant="outline"
          className="border-destructive/50 text-destructive"
          destructive
          title="توقف این عملیات؟"
          description="کارهای اجرانشده لغو می‌شوند و آگهی‌هایی که هنوز فرستاده نشده‌اند کنار گذاشته می‌شوند. آگهی‌هایی که از قبل ساخته یا ویرایش شده‌اند در تلگرام می‌مانند."
          confirmLabel="توقف عملیات"
          onConfirm={() => stop.mutateAsync()}
        >
          <OctagonXIcon /> توقف عملیات
        </ConfirmButton>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">آگهی‌ها</CardTitle>
          <CardDescription>وضعیت هر آگهی بعد از هر همگام‌سازی به‌روز می‌شود.</CardDescription>
          <CardAction>
            <Button size="sm" variant="ghost" onClick={() => refetch()}>
              <RotateCcwIcon /> تازه کردن
            </Button>
          </CardAction>
        </CardHeader>
        <CardContent className="grid gap-2">
          {op.items?.map((it) => (
            <div key={it.id} className="min-w-0 space-y-1.5 rounded-lg border p-3 text-sm" data-testid="bulk-item">
              <div className="flex flex-wrap items-center gap-2">
                <Link to={`/accounts/${account.slug}/ads/${it.ad_id}`} className="font-medium hover:underline">
                  {it.label}
                </Link>
                <span className="num text-xs text-muted-foreground">#{it.ad_id}</span>
                {it.new_ad_id && (
                  <Link to={`/accounts/${account.slug}/ads/${it.new_ad_id}`} className="num inline-flex items-center gap-1 text-xs text-info hover:underline">
                    <ExternalLinkIcon className="size-3" /> آگهی جدید #{it.new_ad_id}
                  </Link>
                )}
                <Badge variant={ITEM_VARIANT[it.status]} className="ms-auto">
                  {it.status_label}
                </Badge>
              </div>
              {it.status === "approved" && STEP_LABEL[it.step] && (
                <p className="text-xs text-muted-foreground">مرحله: {STEP_LABEL[it.step]}</p>
              )}
              {it.withdrawn > 0 && (
                <p className="num text-xs text-muted-foreground">برداشت از آگهی قدیمی: {money(it.withdrawn, cur)}</p>
              )}
              {it.decline_reason && (
                <Alert variant="destructive">
                  <AlertTitle>دلیل رد تلگرام</AlertTitle>
                  <AlertDescription>
                    <p>{it.decline_reason}</p>
                    {op.status !== "stopped" && it.status === "declined" && (
                      <Button size="sm" variant="outline" className="mt-1" onClick={() => setResubmit(it)}>
                        ویرایش و ارسال دوباره
                      </Button>
                    )}
                  </AlertDescription>
                </Alert>
              )}
              {it.error && <p className="text-xs text-destructive">{it.error}</p>}
            </div>
          ))}
        </CardContent>
      </Card>
      {resubmit && <ResubmitDialog account={account} opId={op.id} item={resubmit} onClose={() => setResubmit(null)} />}
    </div>
  );
}

/** Replacements limited by the account balance go out in waves: progress of each one. */
function Waves({ op }: { op: BulkOperation }) {
  const items = (op.items ?? []).filter((i) => i.status !== "skipped" || i.wave);
  const waves = new Map<number, { total: number; done: number }>();
  for (const it of items) {
    if (!it.wave) continue;
    const w = waves.get(it.wave) ?? { total: 0, done: 0 };
    w.total += 1;
    if (["retired", "failed", "declined"].includes(it.status) || (op.mode === "inplace" && it.status === "approved")) w.done += 1;
    waves.set(it.wave, w);
  }
  const waiting = (op.items ?? []).filter((i) => i.status === "pending").length;
  if (op.mode !== "replace" || (waves.size <= 1 && !op.params.waiting_balance)) return null;
  return (
    <div className="flex flex-wrap gap-1.5 text-xs" data-testid="bulk-waves">
      {[...waves.entries()]
        .sort((a, b) => a[0] - b[0])
        .map(([n, w]) => (
          <Badge key={n} variant={w.done === w.total ? "success" : "info"} className="num">
            موج {num(n)}: {num(w.done)} از {num(w.total)}
          </Badge>
        ))}
      {waiting > 0 && (
        <Badge variant="secondary" className="num">
          منتظر موج بعد: {num(waiting)}
        </Badge>
      )}
    </div>
  );
}

function ResubmitDialog({
  account,
  opId,
  item,
  onClose,
}: {
  account: Account;
  opId: number;
  item: BulkItem;
  onClose: () => void;
}) {
  const [text, setText] = useState(item.text ?? "");
  const save = useAction<void, { message: string }>({
    fn: () => api.post(`${accountApi(account.slug)}/bulk/${opId}/items/${item.id}/resubmit`, { text: text.trim() }),
    invalidate: [keys.acc(account.slug)],
    onSuccess: onClose,
  });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>ویرایش و ارسال دوباره</DialogTitle>
          <DialogDescription>
            متن آگهی ردشده اصلاح و دوباره برای بررسی تلگرام فرستاده می‌شود. دلیل رد: {item.decline_reason}
          </DialogDescription>
        </DialogHeader>
        <TextEditor
          slug={account.slug}
          id="resubmit-text"
          value={text}
          onChange={setText}
          adId={item.new_ad_id ?? item.ad_id}
          reason={item.decline_reason}
        />
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            انصراف
          </Button>
          <Button onClick={() => save.mutate()} disabled={save.isPending || !text.trim() || text.trim().length > 160}>
            ارسال دوباره
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
