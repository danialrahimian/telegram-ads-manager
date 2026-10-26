import { useQueryClient } from "@tanstack/react-query";
import { ArrowRightIcon, BotIcon, ImageIcon, MegaphoneIcon, SendIcon } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { toast } from "sonner";
import { Field } from "@/components/field";
import { useAccountCtx } from "@/components/layout/account-layout";
import { PageHeader } from "@/components/page-header";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { api, errorMessage } from "@/lib/api";
import { supportsPhoto } from "@/lib/bulk";
import { money, num } from "@/lib/format";
import { accountApi, keys, useTags, useTexts } from "@/lib/queries";
import { cn } from "@/lib/utils";

export default function AdNewPage() {
  const account = useAccountCtx();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [params] = useSearchParams();
  const tags = useTags(account.slug);
  const texts = useTexts(account.slug);
  const st = account.settings;

  const [kind, setKind] = useState<"channel" | "bot">("channel");
  const [username, setUsername] = useState("");
  const [text, setText] = useState("");
  const [variantId, setVariantId] = useState(params.get("text_id") ?? "none");
  const [tagId, setTagId] = useState("none");
  const [cpm, setCpm] = useState(String(st.default_cpm_channel));
  const [budget, setBudget] = useState(String(st.default_budget));
  const [title, setTitle] = useState("");
  const [note, setNote] = useState("");
  const [userpic, setUserpic] = useState(false);
  const [photo, setPhoto] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const replace = params.get("replace");

  useEffect(() => setCpm(String(kind === "bot" ? st.default_cpm_bot : st.default_cpm_channel)), [kind, st]);
  useEffect(() => {
    if (variantId === "none") return;
    const v = texts.data?.find((t) => String(t.id) === variantId);
    if (v) setText(v.text);
  }, [variantId, texts.data]);

  const tag = tags.data?.find((t) => String(t.id) === tagId);
  const url = useMemo(() => {
    if (!account.bot.username) return "";
    return `https://t.me/${account.bot.username}${tag ? `?start=${tag.start_code}` : ""}`;
  }, [account.bot.username, tag]);
  const tooLong = text.length > 160;
  const maxBudget = st.topup_max_per_ad * 5;
  const minBudget = st.min_initial_budget ?? 1;
  const budgetError =
    budget.trim() === ""
      ? undefined
      : Number(budget) < minBudget
        ? `بودجه‌ی اولیه دست‌کم ${money(minBudget, account.telegram.currency)} است؛ تلگرام آگهی با بودجه‌ی کمتر نمی‌سازد.`
        : Number(budget) > maxBudget
          ? `حداکثر ${money(maxBudget)}`
          : undefined;
  const photoAllowed = supportsPhoto({ placement: kind === "channel" ? "channel_post" : "bot_banner" });
  const valid =
    /^@?[A-Za-z0-9_]{4,32}$/.test(username.trim()) &&
    text.trim().length > 0 &&
    !tooLong &&
    Number(cpm) > 0 &&
    Number(budget) >= minBudget &&
    Number(budget) <= maxBudget &&
    !!account.bot.username;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append(
        "payload",
        JSON.stringify({
          kind,
          username: username.trim(),
          text: text.trim(),
          cpm: Number(cpm),
          budget: Number(budget),
          title: title.trim() || null,
          variant_id: variantId === "none" ? null : Number(variantId),
          tag_id: tag ? tag.id : null,
          show_userpic: userpic,
          note: note.trim() || null,
          replace_ad_id: replace ? Number(replace) : null,
        }),
      );
      if (photo && photoAllowed) fd.append("photo", photo);
      const res = await api.post<{ ad_id: number; message: string }>(`${accountApi(account.slug)}/ads`, fd);
      toast.success(res.message);
      await qc.invalidateQueries({ queryKey: keys.acc(account.slug) });
      nav(`/accounts/${account.slug}/ads/${res.ad_id}`);
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <Button asChild variant="ghost" size="sm" className="text-muted-foreground">
        <Link to=".." relative="path">
          <ArrowRightIcon /> آگهی‌ها
        </Link>
      </Button>
      <PageHeader
        title={replace ? `آگهی جایگزین برای #${replace}` : "آگهی جدید"}
        description="آگهی با بودجه‌ی اولیه ساخته و برای بررسی تلگرام فرستاده می‌شود. لینک آن به ربات همین حساب است."
      />
      {!account.bot.username && (
        <Alert variant="warning">
          <AlertDescription>
            نام کاربری ربات این حساب تنظیم نشده است.{" "}
            <Link className="underline" to={`/accounts/${account.slug}/settings`}>
              تنظیم در صفحه‌ی اتصال
            </Link>
          </AlertDescription>
        </Alert>
      )}

      <form onSubmit={submit} className="grid gap-5">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">جایگاه</CardTitle>
            <CardDescription>آگهی کجا نمایش داده شود؟</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <div className="grid grid-cols-2 gap-2" role="radiogroup">
              {(
                [
                  ["channel", "کانال", MegaphoneIcon],
                  ["bot", "ربات", BotIcon],
                ] as const
              ).map(([k, label, Icon]) => (
                <button
                  key={k}
                  type="button"
                  role="radio"
                  aria-checked={kind === k}
                  onClick={() => setKind(k)}
                  className={cn(
                    "flex items-center justify-center gap-2 rounded-lg border px-3 py-3 text-sm transition",
                    kind === k ? "border-primary bg-accent font-medium text-accent-foreground" : "hover:bg-muted/60",
                  )}
                >
                  <Icon className="size-4" /> {label}
                </button>
              ))}
            </div>
            <Field
              label={kind === "channel" ? "نام کاربری کانال" : "نام کاربری ربات"}
              htmlFor="username"
              hint="کانال یا رباتی که آگهی در آن نمایش داده می‌شود"
            >
              <Input
                id="username"
                dir="ltr"
                className="text-left"
                placeholder="@examplechannel"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </Field>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">متن و لینک</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4">
            <Field label="از کتابخانه‌ی متن‌ها" hint="یا متن را مستقیم بنویسید">
              <Select value={variantId} onValueChange={setVariantId}>
                <SelectTrigger className="w-full">
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
            <Field
              label="متن آگهی"
              htmlFor="text"
              error={tooLong ? "متن آگهی تلگرام حداکثر ۱۶۰ حرف است." : undefined}
              hint={<span className="num">{num(text.length)} / ۱۶۰</span>}
            >
              <Textarea id="text" rows={4} value={text} onChange={(e) => setText(e.target.value)} aria-invalid={tooLong} />
            </Field>
            <Field label="برچسب رفرال (برای ردیابی فروش)" hint="هر آگهی را با لینک دعوت یک حساب معرف جدا بسازید تا فروشش جدا دیده شود.">
              <Select value={tagId} onValueChange={setTagId}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">بدون برچسب</SelectItem>
                  {tags.data?.map((t) => (
                    <SelectItem key={t.id} value={String(t.id)}>
                      {t.name} {t.ads.length ? `(${num(t.ads.length)} آگهی)` : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            {url && (
              <div className="rounded-lg bg-muted/50 px-3 py-2 text-xs">
                <span className="text-muted-foreground">لینک آگهی: </span>
                <span className="ltr">{url}</span>
              </div>
            )}
            {photoAllowed ? (
              <Field label="عکس (اختیاری)" hint="JPG یا PNG، کمتر از ۵ مگابایت">
                <label className="flex cursor-pointer items-center gap-2 rounded-md border border-dashed px-3 py-2.5 text-sm text-muted-foreground hover:bg-muted/50">
                  <ImageIcon className="size-4" />
                  <span className="truncate">{photo ? photo.name : "انتخاب عکس"}</span>
                  <input
                    type="file"
                    accept="image/jpeg,image/png"
                    className="sr-only"
                    onChange={(e) => setPhoto(e.target.files?.[0] ?? null)}
                  />
                </label>
              </Field>
            ) : (
              <>
                <p className="text-xs text-muted-foreground">آگهی ربات عکس ندارد؛ عکس فقط برای آگهی کانال است.</p>
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm">نمایش عکس پروفایل ربات</span>
                  <Switch checked={userpic} onCheckedChange={setUserpic} aria-label="نمایش عکس پروفایل" />
                </div>
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">قیمت و بودجه</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2">
            <Field label={`CPM (${account.telegram.currency || "ارز حساب"})`} htmlFor="cpm" hint="هزینه‌ی هر هزار نمایش">
              <Input id="cpm" type="number" step="0.01" min="0.01" dir="ltr" value={cpm} onChange={(e) => setCpm(e.target.value)} />
            </Field>
            <Field
              label="بودجه‌ی اولیه"
              htmlFor="budget"
              error={budgetError}
              hint={`موجودی حساب: ${money(account.telegram.remaining_budget, account.telegram.currency)}`}
            >
              <Input
                id="budget"
                type="number"
                step="0.01"
                min={minBudget}
                dir="ltr"
                value={budget}
                onChange={(e) => setBudget(e.target.value)}
                aria-invalid={!!budgetError}
              />
            </Field>
            <Field label="عنوان داخلی (اختیاری)" htmlFor="title">
              <Input id="title" value={title} onChange={(e) => setTitle(e.target.value)} />
            </Field>
            <Field label="یادداشت (اختیاری)" htmlFor="note">
              <Input id="note" value={note} onChange={(e) => setNote(e.target.value)} />
            </Field>
          </CardContent>
        </Card>

        <div className="flex justify-end gap-2">
          <Button asChild variant="outline">
            <Link to=".." relative="path">انصراف</Link>
          </Button>
          <Button type="submit" disabled={!valid || busy}>
            <SendIcon /> {busy ? "در حال ساخت…" : "ساخت و ارسال برای بررسی"}
          </Button>
        </div>
      </form>
    </div>
  );
}
