import { FlaskConicalIcon } from "lucide-react";
import { useState } from "react";
import { CopyButton } from "@/components/copy-button";
import { Field } from "@/components/field";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { accountApi, keys, useAction } from "@/lib/queries";
import type { Account, AdRow, EditProbeResult } from "@/lib/types";

export const EDIT_FORMAT_LABEL: Record<string, string> = {
  json: "JSON",
  form: "فرم (x-www-form-urlencoded)",
  query: "پارامتر آدرس (query string)",
  none: "اعمال نمی‌شود؛ فقط جایگزینی",
  unknown: "آزمایش نشده",
};

/** Edits this ad's text for real in each documented request format until Telegram stores it. */
export function EditProbe({ account, ad }: { account: Account; ad: AdRow }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(ad.text);
  const [sure, setSure] = useState(false);
  const [result, setResult] = useState<EditProbeResult | null>(null);
  const run = useAction<void, EditProbeResult>({
    fn: () => api.post(`${accountApi(account.slug)}/bulk/edit-probe`, { ad_id: ad.ad_id, text, confirm: true }),
    invalidate: [keys.acc(account.slug)],
    success: (r) => r.message,
    onSuccess: (r) => setResult(r),
  });
  const changed = text.trim() && text.trim() !== ad.text.trim();
  return (
    <>
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
        <FlaskConicalIcon /> آزمایش ویرایش در API
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>آزمایش ویرایش آگهی #{ad.ad_id}</DialogTitle>
            <DialogDescription>
              اگر بعد از ویرایش، تلگرام «ok» داد ولی متن آگهی عوض نشد، این آزمایش همان ویرایش را با هر سه روش مستند API
              (JSON، فرم، پارامتر آدرس) می‌فرستد و بعد از هر بار آگهی را از تلگرام می‌خواند. اولین روشی که متن را واقعاً عوض
              کند برای همه‌ی ویرایش‌های بعدی ذخیره می‌شود.
            </DialogDescription>
          </DialogHeader>
          {!result ? (
            <div className="grid gap-3">
              <Field label="متن تازه‌ی این آگهی" htmlFor="probe-text" hint={`${text.length} از ۱۶۰ کاراکتر`}>
                <Textarea id="probe-text" rows={3} value={text} onChange={(e) => setText(e.target.value)} />
              </Field>
              <div className="flex items-start gap-2">
                <Checkbox id="probe-sure" checked={sure} onCheckedChange={(v) => setSure(v === true)} className="mt-0.5" />
                <Label htmlFor="probe-sure" className="leading-relaxed">
                  می‌دانم متن این آگهی در تلگرام واقعاً عوض می‌شود و آگهی دوباره بررسی می‌شود.
                </Label>
              </div>
            </div>
          ) : (
            <ProbeTrace result={result} />
          )}
          <DialogFooter>
            {!result ? (
              <Button onClick={() => run.mutate()} disabled={!sure || !changed || text.length > 160 || run.isPending}>
                <FlaskConicalIcon /> {run.isPending ? "در حال آزمایش…" : "آزمایش و ویرایش"}
              </Button>
            ) : (
              <Button variant="outline" onClick={() => setOpen(false)}>
                بستن
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

export function ProbeTrace({ result }: { result: EditProbeResult }) {
  return (
    <div className="grid min-w-0 gap-2 text-sm" data-testid="edit-probe">
      <Alert variant={result.result === "none" ? "destructive" : result.result === "pending" ? "info" : "success"}>
        <AlertDescription>{result.message}</AlertDescription>
      </Alert>
      {result.trace.map((t) => (
        <div key={t.format} className="min-w-0 space-y-1 rounded-lg border p-2 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline" className="ltr">
              {t.content_type}
            </Badge>
            <Badge variant={t.applied ? "success" : "secondary"}>{t.applied ? "اعمال شد" : "اعمال نشد"}</Badge>
          </div>
          <div className="text-muted-foreground">
            پاسخ تلگرام:{" "}
            <span className="ltr">{t.answer.ok ? `ok · status=${t.answer.status ?? "—"}` : `error=${t.answer.error}`}</span>
          </div>
          <div className="wrap-anywhere">
            متن خوانده‌شده بعد از ویرایش: <span dir="auto">«{t.read_back.text ?? "—"}»</span>{" "}
            <span className="ltr text-muted-foreground">({t.read_back.status ?? "—"})</span>
          </div>
        </div>
      ))}
      <span className="flex items-center gap-1 text-xs text-muted-foreground">
        گزارش کامل (بدون توکن) <CopyButton value={JSON.stringify(result.trace, null, 2)} label="کپی گزارش آزمایش" />
      </span>
    </div>
  );
}
