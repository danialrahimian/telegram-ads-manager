import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangleIcon, ArrowLeftIcon, ImageIcon, InfoIcon, ShieldCheckIcon, TagIcon } from "lucide-react";
import { CopyButton } from "@/components/copy-button";
import { useState } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";
import { Field } from "@/components/field";
import { TextEditor } from "@/components/text-editor";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { api, errorMessage } from "@/lib/api";
import { BULK_KIND_LABEL, supportsPhoto } from "@/lib/bulk";
import { money, num } from "@/lib/format";
import { accountApi, keys, useAction, useCapabilities, useTexts, useTracking } from "@/lib/queries";
import type { Account, BulkKind, BulkMode, BulkOperation, BulkPreview, BulkRequest, Capabilities } from "@/lib/types";
import { cn } from "@/lib/utils";

export interface BulkTarget {
  ad_id: number;
  label: string;
  text: string;
  reason?: string;
  promote_url?: string;
  placement?: string;
  target_type?: string;
}

type ReferralMode = "pool" | "shared" | "random";

const CAP_TEXT: Record<string, string> = {
  yes: "API عوض کردن عکس آگهی موجود را پشتیبانی می‌کند؛ هر دو حالت ممکن است.",
  no: "API عکس آگهی موجود را عوض نمی‌کند؛ عکس فقط با حالت «جایگزینی» عوض می‌شود.",
  unknown: "هنوز معلوم نیست API عکس آگهی موجود را عوض می‌کند؛ عکس در جا فقط روی یک آگهی (به‌عنوان آزمایش) ممکن است، برای بقیه «جایگزینی».",
};

export function BulkDialog({
  account,
  targets,
  kind,
  proposalIds,
  onClose,
}: {
  account: Account;
  targets: BulkTarget[];
  kind: BulkKind;
  proposalIds?: number[];
  onClose: () => void;
}) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const base = accountApi(account.slug);
  const st = account.settings;
  const cur = account.telegram.currency;
  const caps = useCapabilities(account.slug);
  const texts = useTexts(account.slug);
  const [step, setStep] = useState<"config" | "preview">("config");
  const [mode, setMode] = useState<BulkMode>("replace");
  const [text, setText] = useState(kind === "replace" && targets.length ? targets[0].text : "");
  const [variantId, setVariantId] = useState("none");
  const [perAd, setPerAd] = useState(false);
  const [perAdTexts, setPerAdTexts] = useState<Record<number, string>>(() =>
    Object.fromEntries(targets.map((t) => [t.ad_id, t.text])),
  );
  const [photo, setPhoto] = useState<{ id: string; name: string } | null>(null);
  const [uploading, setUploading] = useState(false);
  const [budgetSource, setBudgetSource] = useState<"account" | "transfer">("transfer");
  const minBudget = st.min_initial_budget ?? 1;
  const [initialBudget, setInitialBudget] = useState(String(Math.max(st.default_budget, minBudget)));
  const [refMode, setRefMode] = useState<ReferralMode>("pool");
  const [sharedRef, setSharedRef] = useState("");
  const [sharedTag, setSharedTag] = useState("none");
  const tracking = useTracking(account.slug);
  const [topupAfter, setTopupAfter] = useState(false);
  const [groupBy, setGroupBy] = useState<"target" | "cpm" | "placement">("target");
  const [preview, setPreview] = useState<BulkPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const effMode: BulkMode = kind === "replace" ? "replace" : mode;

  const body = (): BulkRequest => ({
    kind,
    ad_ids: targets.map((t) => t.ad_id),
    mode: effMode,
    ...(kind !== "referral" && !perAd && text.trim() ? { text: text.trim() } : {}),
    ...(kind !== "referral" && !perAd && variantId !== "none" && !text.trim() ? { variant_id: Number(variantId) } : {}),
    ...(perAd ? { per_ad: Object.fromEntries(Object.entries(perAdTexts).map(([k, v]) => [k, { text: v.trim() }])) } : {}),
    ...(photo ? { photo_id: photo.id, photo_name: photo.name } : {}),
    budget_source: budgetSource,
    ...(budgetSource === "account" ? { initial_budget: Number(initialBudget) } : {}),
    topup_after_withdraw: budgetSource === "account" && topupAfter,
    group_by: groupBy,
    ...(kind === "referral" ? { referral_mode: refMode } : {}),
    ...(kind === "referral" && refMode === "shared" && sharedTag !== "none" ? { shared_tag_id: Number(sharedTag) } : {}),
    ...(kind === "referral" && refMode === "shared" && sharedTag === "none" && sharedRef.trim()
      ? { shared_referrer_id: sharedRef.trim() }
      : {}),
    ...(proposalIds?.length ? { proposal_ids: proposalIds } : {}),
  });

  const upload = async (file: File | undefined) => {
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) return toast.error("عکس باید کمتر از ۵ مگابایت باشد.");
    if (!["image/jpeg", "image/png"].includes(file.type)) return toast.error("عکس باید JPG یا PNG باشد.");
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append("photo", file);
      const r = await api.post<{ photo_id: string; name: string }>(`${base}/bulk/photo`, fd);
      setPhoto({ id: r.photo_id, name: file.name });
      toast.success("عکس یک بار در تلگرام بارگذاری شد و برای همه‌ی آگهی‌ها استفاده می‌شود.");
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setUploading(false);
    }
  };

  const doPreview = async () => {
    setBusy(true);
    try {
      setPreview(await api.post<BulkPreview>(`${base}/bulk/preview`, body()));
      setStep("preview");
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const run = useAction<void, BulkOperation & { message: string }>({
    fn: () => api.post(`${base}/bulk`, { ...body(), confirm: true }),
    onSuccess: async (op) => {
      await qc.invalidateQueries({ queryKey: keys.acc(account.slug) });
      onClose();
      nav(`/accounts/${account.slug}/bulk/${op.id}`);
    },
  });
  const probe = useAction<void, Capabilities>({
    fn: () => api.post(`${base}/bulk/capabilities/probe`),
    invalidate: [[...keys.acc(account.slug), "capabilities"]],
    success: "پشتیبانی API بدون دست زدن به آگهی‌ها آزمایش شد.",
  });

  const editCap = caps.data?.edit_photo ?? "unknown";
  const deleteCap = caps.data?.delete_ad ?? "unknown";
  const photoAds = targets.filter(supportsPhoto).length;
  const budgetTooLow = effMode === "replace" && budgetSource === "account" && Number(initialBudget) < minBudget;
  const canConfig =
    !budgetTooLow &&
    (kind === "referral"
      ? refMode !== "shared" || sharedTag !== "none" || /^\d{3,20}$/.test(sharedRef.trim())
      : perAd
        ? Object.values(perAdTexts).every((v) => v.trim())
        : !!(text.trim() || variantId !== "none" || photo));
  const randomResult = tracking.data?.random.result ?? account.settings.random_tracking ?? "unknown";

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            {BULK_KIND_LABEL[kind]} — {num(targets.length)} آگهی
          </DialogTitle>
          <DialogDescription>
            اول پیش‌نمایش دقیق تغییرها و پول جابه‌جاشده را ببینید؛ هیچ کاری بدون تأیید صریح شما اجرا نمی‌شود.
          </DialogDescription>
        </DialogHeader>

        {step === "config" && (
          <div className="grid gap-4">
            {kind !== "replace" && (
              <Field label="حالت اجرا">
                <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="حالت اجرا">
                  {(
                    [
                      ["replace", "جایگزینی (پیش‌فرض)", "آگهی تازه با همان جایگاه و CPM؛ آگهی قدیمی فقط بعد از تأیید تلگرام متوقف و بایگانی می‌شود."],
                      ["inplace", "ویرایش در جا", "همان آگهی ویرایش می‌شود؛ آمار حفظ می‌شود ولی تا تأیید دوباره نمایش داده نمی‌شود."],
                    ] as const
                  ).map(([k, label, desc]) => (
                    <button
                      key={k}
                      type="button"
                      role="radio"
                      aria-checked={mode === k}
                      onClick={() => setMode(k)}
                      className={cn(
                        "rounded-lg border p-3 text-start text-sm transition hover:bg-muted/60",
                        mode === k && "border-primary bg-accent/60",
                      )}
                    >
                      <div className="font-medium">{label}</div>
                      <div className="mt-1 text-xs leading-relaxed text-muted-foreground">{desc}</div>
                    </button>
                  ))}
                </div>
              </Field>
            )}

            {kind === "referral" && (
              <>
                <Field label="نوع لینک رفرال">
                  <div className="grid gap-2" role="radiogroup" aria-label="نوع لینک رفرال">
                    {(
                      [
                        ["pool", "از استخر معرف‌ها", "به هر آگهی (یا گروه آگهی) یک معرف آزاد از استخر (تنظیمات حساب) می‌رسد."],
                        ["shared", "یک لینک مشترک برای همه", "همه‌ی آگهی‌های انتخاب‌شده لینک یک معرف یا برچسب را می‌گیرند، حتی اگر از قبل روی آگهی‌های دیگر باشد."],
                        ["random", "لینک رندوم اختصاصی برای هر آگهی", "برای هر آگهی یک کد عددی تصادفی و یکتا ساخته می‌شود، با برچسب خودش."],
                      ] as const
                    ).map(([k, label, desc]) => (
                      <button
                        key={k}
                        type="button"
                        role="radio"
                        aria-checked={refMode === k}
                        onClick={() => setRefMode(k)}
                        className={cn("rounded-lg border p-3 text-start text-sm transition hover:bg-muted/60", refMode === k && "border-primary bg-accent/60")}
                      >
                        <div className="font-medium">{label}</div>
                        <div className="mt-1 text-xs leading-relaxed text-muted-foreground">{desc}</div>
                      </button>
                    ))}
                  </div>
                </Field>
                {refMode === "pool" && (
                  <>
                    <Alert variant="info">
                      <TagIcon />
                      <AlertDescription>
                        <p>
                          لینک هر آگهی <span className="ltr">?start=شناسه‌ی معرف</span> می‌شود. اگر معرف کمتر از آگهی باشد، آگهی‌ها گروه‌بندی
                          می‌شوند و فروش هر گروه فقط برای کل گروه دیده می‌شود.
                        </p>
                      </AlertDescription>
                    </Alert>
                    <Field label="گروه‌بندی وقتی معرف کم است">
                      <Select value={groupBy} onValueChange={(v) => setGroupBy(v as typeof groupBy)}>
                        <SelectTrigger className="w-full" aria-label="گروه‌بندی">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="target">بر اساس کانال/ربات</SelectItem>
                          <SelectItem value="cpm">بر اساس CPM</SelectItem>
                          <SelectItem value="placement">بر اساس نوع جایگاه</SelectItem>
                        </SelectContent>
                      </Select>
                    </Field>
                  </>
                )}
                {refMode === "shared" && (
                  <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="برچسب موجود">
                      <Select value={sharedTag} onValueChange={setSharedTag}>
                        <SelectTrigger className="w-full" aria-label="برچسب موجود">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="none">— شناسه‌ی معرف را بنویسید —</SelectItem>
                          {tracking.data?.tags.map((t) => (
                            <SelectItem key={t.id} value={String(t.id)}>
                              {t.name} ({t.start_code})
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </Field>
                    <Field label="یا شناسه‌ی معرف (از استخر یا دستی)" htmlFor="shared-ref">
                      <Input
                        id="shared-ref"
                        dir="ltr"
                        inputMode="numeric"
                        list="pool-ids"
                        disabled={sharedTag !== "none"}
                        value={sharedRef}
                        onChange={(e) => setSharedRef(e.target.value.replace(/\D/g, ""))}
                      />
                      <datalist id="pool-ids">
                        {tracking.data?.pool.map((p) => (
                          <option key={p.referrer_id} value={p.referrer_id} />
                        ))}
                      </datalist>
                    </Field>
                    <p className="text-xs text-muted-foreground sm:col-span-2">فروش فقط برای کل این گروه دیده می‌شود، نه برای هر آگهی.</p>
                  </div>
                )}
                {refMode === "random" && (
                  <Alert variant={randomResult === "works" ? "success" : "warning"}>
                    <AlertTriangleIcon />
                    <AlertTitle>
                      {randomResult === "works"
                        ? "آزمایش نشان داد ربات کاربران کد رندوم را ثبت می‌کند؛ فروش هر آگهی جدا دیده می‌شود."
                        : randomResult === "ignored"
                          ? "آزمایش نشان داد ربات کاربرانی را که با کد ناشناخته می‌آیند ثبت نمی‌کند."
                          : "هنوز معلوم نیست ربات کاربرانی را که با کد ناشناخته می‌آیند ثبت می‌کند."}
                    </AlertTitle>
                    {randomResult !== "works" && (
                      <AlertDescription>
                        <p>
                          ربات فعلاً فقط وقتی کاربر را به یک منبع نسبت می‌دهد که start شناسه‌ی یک کاربر موجود باشد. لینک‌ها کار می‌کنند، ولی
                          ممکن است فروش این آگهی‌ها در پنل جدا دیده نشود. پیشنهاد: اول «آزمایش ردیابی کد رندوم» را در تنظیمات حساب انجام دهید.
                        </p>
                        {tracking.data?.campaign_request && (
                          <span className="flex items-center gap-1 text-xs">
                            متن درخواست برای توسعه‌دهنده‌ی ربات <CopyButton value={tracking.data.campaign_request} label="کپی متن درخواست" />
                          </span>
                        )}
                      </AlertDescription>
                    )}
                  </Alert>
                )}
              </>
            )}

            {kind !== "referral" && (
              <>
                {kind === "replace" && targets.length > 1 && (
                  <div className="flex items-center justify-between gap-3 rounded-lg border p-3">
                    <Label htmlFor="per-ad">متن جدا برای هر آگهی</Label>
                    <Switch id="per-ad" checked={perAd} onCheckedChange={setPerAd} />
                  </div>
                )}
                {kind === "replace" && targets.some((t) => t.reason) && !perAd && (
                  <Alert variant="warning">
                    <AlertTriangleIcon />
                    <AlertTitle>دلیل پیشنهاد</AlertTitle>
                    <AlertDescription>
                      {targets.slice(0, 3).map((t) => (
                        <p key={t.ad_id}>
                          <span className="font-medium">{t.label}:</span> {t.reason}
                        </p>
                      ))}
                    </AlertDescription>
                  </Alert>
                )}
                {perAd ? (
                  <div className="grid gap-4">
                    {targets.map((t) => (
                      <div key={t.ad_id} className="space-y-1 rounded-lg border p-3">
                        <div className="text-sm font-medium">{t.label}</div>
                        {t.reason && <p className="text-xs text-muted-foreground">{t.reason}</p>}
                        <TextEditor
                          slug={account.slug}
                          id={`per-ad-${t.ad_id}`}
                          label="متن تازه"
                          value={perAdTexts[t.ad_id] ?? ""}
                          onChange={(v) => setPerAdTexts((x) => ({ ...x, [t.ad_id]: v }))}
                          adId={t.ad_id}
                          reason={t.reason}
                        />
                      </div>
                    ))}
                  </div>
                ) : (
                  <>
                    {kind === "content" && (
                      <Field label="از کتابخانه‌ی متن‌ها (اختیاری)">
                        <Select
                          value={variantId}
                          onValueChange={(v) => {
                            setVariantId(v);
                            const t = texts.data?.find((x) => String(x.id) === v);
                            if (t) setText(t.text);
                          }}
                        >
                          <SelectTrigger className="w-full" aria-label="متن کتابخانه">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="none">— متن دستی —</SelectItem>
                            {texts.data
                              ?.filter((t) => t.active)
                              .map((t) => (
                                <SelectItem key={t.id} value={String(t.id)}>
                                  {t.name}
                                </SelectItem>
                              ))}
                          </SelectContent>
                        </Select>
                      </Field>
                    )}
                    <TextEditor
                      slug={account.slug}
                      id="bulk-text"
                      label={kind === "content" ? "متن تازه (خالی = متن فعلی بماند)" : "متن آگهی جایگزین"}
                      value={text}
                      onChange={(v) => {
                        setText(v);
                        setVariantId("none");
                      }}
                      adId={targets[0]?.ad_id}
                      reason={targets
                        .map((t) => t.reason)
                        .filter(Boolean)
                        .join(" | ")}
                    />
                  </>
                )}
                <Field
                  label="عکس تازه (اختیاری، JPG/PNG کمتر از ۵ مگابایت)"
                  htmlFor="bulk-photo"
                  hint={
                    <>
                      {photoAds < targets.length &&
                        `عکس فقط روی ${num(photoAds)} آگهی کانال اعمال می‌شود؛ ${num(targets.length - photoAds)} آگهی ربات عکس ندارند. `}
                      {CAP_TEXT[editCap]}
                    </>
                  }
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <Input
                      id="bulk-photo"
                      type="file"
                      accept="image/jpeg,image/png"
                      className="max-w-full sm:max-w-xs"
                      disabled={uploading}
                      onChange={(e) => upload(e.target.files?.[0])}
                    />
                    {photo && (
                      <Badge variant="success">
                        <ImageIcon /> {photo.name}
                      </Badge>
                    )}
                  </div>
                </Field>
              </>
            )}

            {effMode === "replace" && (
              <Field label="بودجه‌ی آگهی جدید">
                <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="منبع بودجه">
                  {(
                    [
                      ["transfer", "انتقال از آگهی قدیمی", `با حداقل بودجه‌ی تلگرام (${money(minBudget, cur)}) از موجودی ساخته می‌شود؛ بعد از تأیید و برداشت از قدیمی، فقط مازاد بودجه‌ی قدیمی بر این حداقل به آگهی جدید شارژ می‌شود.`],
                      ["account", "از موجودی حساب", "بودجه‌ی اولیه از موجودی حساب کم می‌شود؛ بودجه‌ی قدیمی به موجودی برمی‌گردد."],
                    ] as const
                  ).map(([k, label, desc]) => (
                    <button
                      key={k}
                      type="button"
                      role="radio"
                      aria-checked={budgetSource === k}
                      onClick={() => setBudgetSource(k)}
                      className={cn(
                        "rounded-lg border p-3 text-start text-sm transition hover:bg-muted/60",
                        budgetSource === k && "border-primary bg-accent/60",
                      )}
                    >
                      <div className="font-medium">{label}</div>
                      <div className="mt-1 text-xs leading-relaxed text-muted-foreground">{desc}</div>
                    </button>
                  ))}
                </div>
              </Field>
            )}
            {effMode === "replace" && budgetSource === "account" && (
              <div className="grid gap-3 sm:grid-cols-2">
                <Field
                  label={`بودجه‌ی اولیه‌ی هر آگهی (${cur || "ارز حساب"})`}
                  htmlFor="bulk-budget"
                  error={budgetTooLow ? `حداقل ${money(minBudget, cur)}؛ تلگرام آگهی با بودجه‌ی کمتر نمی‌سازد.` : undefined}
                >
                  <Input
                    id="bulk-budget"
                    type="number"
                    dir="ltr"
                    min={minBudget}
                    step={0.1}
                    value={initialBudget}
                    onChange={(e) => setInitialBudget(e.target.value)}
                  />
                </Field>
                <div className="flex items-center justify-between gap-3 rounded-lg border p-3">
                  <Label htmlFor="topup-after" className="leading-relaxed">
                    بودجه‌ی برداشت‌شده هم به آگهی جدید شارژ شود
                  </Label>
                  <Switch id="topup-after" checked={topupAfter} onCheckedChange={setTopupAfter} />
                </div>
              </div>
            )}
            {effMode === "replace" && (
              <p className="flex items-start gap-1.5 text-xs leading-relaxed text-muted-foreground">
                <InfoIcon className="mt-0.5 size-3.5 shrink-0" />
                {deleteCap === "yes"
                  ? "آگهی‌های قدیمی بعد از برداشت بودجه حذف می‌شوند (API متد حذف دارد)."
                  : "آگهی‌های قدیمی بعد از برداشت بودجه بایگانی می‌شوند: متوقف، بدون بودجه و پنهان از فهرست (فیلتر «بایگانی‌شده»)."}
              </p>
            )}
            {(editCap === "unknown" || deleteCap === "unknown") && (
              <Button type="button" variant="ghost" size="sm" className="justify-self-start" onClick={() => probe.mutate()} disabled={probe.isPending}>
                <ShieldCheckIcon /> آزمایش امن پشتیبانی API (حذف آگهی، عوض کردن عکس)
              </Button>
            )}
          </div>
        )}

        {step === "preview" && preview && <PreviewView p={preview} currency={cur} />}

        <DialogFooter>
          {step === "config" ? (
            <>
              <Button variant="outline" onClick={onClose}>
                انصراف
              </Button>
              <Button onClick={doPreview} disabled={busy || uploading || !canConfig}>
                {busy ? "در حال محاسبه…" : "پیش‌نمایش"}
              </Button>
            </>
          ) : (
            <>
              <Button variant="outline" onClick={() => setStep("config")}>
                <ArrowLeftIcon className="rotate-180" /> بازگشت و ویرایش
              </Button>
              <Button
                onClick={() => run.mutate()}
                disabled={run.isPending || !preview || preview.errors.length > 0 || preview.totals.ads === 0}
              >
                تأیید و اجرا برای {num(preview?.totals.ads ?? 0)} آگهی
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function PreviewView({ p, currency }: { p: BulkPreview; currency: string }) {
  return (
    <div className="grid min-w-0 gap-3" data-testid="bulk-preview">
      <div className="grid grid-cols-2 gap-2 text-center text-xs sm:grid-cols-4">
        <Mini label="آگهی قابل اجرا" value={num(p.totals.ads)} />
        <Mini label="بدون تغییر / کنار گذاشته" value={num(p.totals.skipped + p.totals.blocked)} />
        {p.mode === "replace" ? (
          <>
            <Mini label="از موجودی حساب" value={money(p.totals.from_account, currency)} />
            <Mini label="بودجه‌ی آگهی‌های جدید" value={money(p.totals.to_new, currency)} />
          </>
        ) : (
          <>
            <Mini label="پول جابه‌جاشده" value="—" />
            <Mini label="حالت" value="ویرایش در جا" />
          </>
        )}
      </div>
      {p.photo_summary && (
        <p className="rounded-md bg-muted/50 px-3 py-2 text-sm" data-testid="photo-summary">
          {p.photo_summary}
        </p>
      )}
      {p.budget_line && (
        <p className="rounded-md bg-muted/50 px-3 py-2 text-sm" data-testid="budget-line">
          {p.budget_line}
          {p.totals.first_wave < p.totals.ads && ` موج اول: ${num(p.totals.first_wave)} آگهی.`}
        </p>
      )}
      {p.mode === "replace" && (
        <p className="text-xs text-muted-foreground">
          موجودی حساب: <span className="num">{money(p.totals.account_balance, currency)}</span> · بودجه‌ی فعلی آگهی‌های قدیمی:{" "}
          <span className="num">{money(p.totals.from_old, currency)}</span>
          {p.move_old_budget ? " (بعد از برداشت به آگهی‌های جدید منتقل می‌شود)" : " (به موجودی حساب برمی‌گردد)"}
        </p>
      )}
      {p.errors.length > 0 && (
        <Alert variant="destructive">
          <AlertTriangleIcon />
          <AlertTitle>قبل از اجرا باید درست شود</AlertTitle>
          <AlertDescription>
            {p.errors.map((e) => (
              <p key={e}>{e}</p>
            ))}
          </AlertDescription>
        </Alert>
      )}
      {p.warnings.length > 0 && (
        <Alert variant="warning">
          <InfoIcon />
          <AlertDescription>
            {p.warnings.map((w) => (
              <p key={w}>{w}</p>
            ))}
          </AlertDescription>
        </Alert>
      )}
      {p.assignments.length > 0 && (
        <div className="space-y-1.5 rounded-lg border p-3 text-xs">
          <div className="font-medium">تخصیص معرف‌ها</div>
          {p.assignments.map((a) => (
            <div key={a.referrer_id} className="flex flex-wrap items-center gap-1.5">
              <Badge variant="info" className="ltr">
                start={a.referrer_id}
              </Badge>
              <span className="text-muted-foreground">{a.label}</span>
              <span className="num">({num(a.ad_ids.length)} آگهی{a.ad_ids.length > 1 ? "، برچسب مشترک" : ""})</span>
            </div>
          ))}
        </div>
      )}
      <div className="grid max-h-80 gap-2 overflow-y-auto pe-1">
        {p.items.map((it) => (
          <div
            key={it.ad_id}
            className={cn("min-w-0 space-y-1.5 rounded-lg border p-3 text-xs", (it.blocked || it.skipped) && "border-dashed opacity-80")}
          >
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-medium">{it.label}</span>
              <span className="num text-xs text-muted-foreground">#{it.ad_id}</span>
              {it.blocked && <Badge variant="secondary">اجرا نمی‌شود</Badge>}
              {it.skipped && <Badge variant="outline">بدون تغییر</Badge>}
            </div>
            {it.skipped ? (
              <p className="text-muted-foreground">{it.skipped}</p>
            ) : it.blocked ? (
              <p className="text-destructive">{it.blocked}</p>
            ) : (
              <>
                {it.changes.map((c) => (
                  <div key={c.field} className="grid gap-0.5">
                    <span className="text-muted-foreground">{c.label}:</span>
                    <span className="line-through opacity-60 wrap-anywhere">{c.old || "—"}</span>
                    <span className="wrap-anywhere">{c.new}</span>
                  </div>
                ))}
                {p.mode === "replace" && (
                  <div className="num text-muted-foreground">
                    از حساب {money(it.money.from_account, currency)} · بودجه‌ی قدیمی {money(it.money.from_old, currency)} · آگهی جدید
                    تا {money(it.money.to_new, currency)}
                  </div>
                )}
                {it.warnings.map((w) => (
                  <p key={w} className="text-warning">
                    {w}
                  </p>
                ))}
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function Mini({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-muted/50 px-1 py-1.5">
      <div className="num truncate font-semibold">{value}</div>
      <div className="truncate text-muted-foreground">{label}</div>
    </div>
  );
}
