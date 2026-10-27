import { BanknoteIcon, MoreVerticalIcon, PauseIcon, PlayIcon, SendIcon, Undo2Icon } from "lucide-react";
import { useState } from "react";
import { Field } from "@/components/field";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { money } from "@/lib/format";
import { accountApi, keys, useAction } from "@/lib/queries";
import type { Account, AdRow } from "@/lib/types";

type Kind = "pause" | "resume" | "withdraw" | "topup" | "submit_review";

export function useAdAction(account: Account) {
  return useAction<{ adId: number; action: Kind; amount?: number }, { message: string }>({
    fn: ({ adId, action, amount }) =>
      api.post(`${accountApi(account.slug)}/ads/${adId}/actions`, { action, amount: amount ?? null }),
    invalidate: [keys.acc(account.slug)],
  });
}

export function AdActions({ account, ad, compact }: { account: Account; ad: AdRow; compact?: boolean }) {
  const act = useAdAction(account);
  const [dialog, setDialog] = useState<null | "topup" | "withdraw">(null);
  const [amount, setAmount] = useState(String(account.settings.topup_max_per_ad));
  const cur = account.telegram.currency;
  const canPause = ad.status === "active" && !ad.is_paused;
  const canResume = ad.is_paused;

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          {compact ? (
            <Button size="icon-sm" variant="ghost" aria-label="اقدام‌ها">
              <MoreVerticalIcon />
            </Button>
          ) : (
            <Button size="sm" variant="outline">
              اقدام‌ها <MoreVerticalIcon />
            </Button>
          )}
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {ad.status === "ready_for_review" && (
            <DropdownMenuItem onSelect={() => act.mutate({ adId: ad.ad_id, action: "submit_review" })}>
              <SendIcon /> ارسال برای بررسی تلگرام
            </DropdownMenuItem>
          )}
          {canPause && (
            <DropdownMenuItem onSelect={() => act.mutate({ adId: ad.ad_id, action: "pause" })}>
              <PauseIcon /> توقف موقت
            </DropdownMenuItem>
          )}
          {canResume && (
            <DropdownMenuItem onSelect={() => act.mutate({ adId: ad.ad_id, action: "resume" })}>
              <PlayIcon /> فعال‌سازی دوباره
            </DropdownMenuItem>
          )}
          <DropdownMenuItem onSelect={() => setDialog("topup")}>
            <BanknoteIcon /> شارژ بودجه
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            variant="destructive"
            disabled={ad.remaining <= 0}
            onSelect={() => setDialog("withdraw")}
          >
            <Undo2Icon /> توقف و پس گرفتن بودجه
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={dialog === "topup"} onOpenChange={(o) => !o && setDialog(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>شارژ آگهی #{ad.ad_id}</DialogTitle>
            <DialogDescription>
              بودجه‌ی فعلی آگهی {money(ad.remaining, cur)} است. سقف هر شارژ{" "}
              {money(account.settings.topup_max_per_ad, cur)} و سقف روزانه {money(account.settings.topup_daily_cap, cur)} است.
            </DialogDescription>
          </DialogHeader>
          <Field label={`مبلغ (${cur || "ارز حساب"})`} htmlFor="topup-amount">
            <Input
              id="topup-amount"
              type="number"
              inputMode="decimal"
              step="0.01"
              min="0.01"
              dir="ltr"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
            />
          </Field>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialog(null)}>
              انصراف
            </Button>
            <Button
              disabled={act.isPending || !(Number(amount) > 0)}
              onClick={() =>
                act.mutate({ adId: ad.ad_id, action: "topup", amount: Number(amount) }, { onSuccess: () => setDialog(null) })
              }
            >
              شارژ
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={dialog === "withdraw"} onOpenChange={(o) => !o && setDialog(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>توقف و پس گرفتن بودجه</DialogTitle>
            <DialogDescription>
              آگهی #{ad.ad_id} متوقف می‌شود. تلگرام فقط بعد از ۱۰ دقیقه توقف اجازه‌ی برداشت می‌دهد؛ بعد از آن{" "}
              {money(ad.remaining, cur)} به موجودی حساب برمی‌گردد.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialog(null)}>
              انصراف
            </Button>
            <Button
              variant="destructive"
              disabled={act.isPending}
              onClick={() => act.mutate({ adId: ad.ad_id, action: "withdraw" }, { onSuccess: () => setDialog(null) })}
            >
              توقف و برداشت
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
