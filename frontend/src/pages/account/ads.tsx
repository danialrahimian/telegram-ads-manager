import {
  ArchiveIcon,
  ArrowDownIcon,
  ArrowUpDownIcon,
  ArrowUpIcon,
  ColumnsIcon,
  LinkIcon,
  ListChecksIcon,
  MegaphoneIcon,
  PencilLineIcon,
  PlusIcon,
  RotateCcwIcon,
  SearchIcon,
  SlidersHorizontalIcon,
  TagIcon,
  XIcon,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { AdActions } from "@/components/ad-actions";
import {
  activeFilterCount,
  COLUMNS,
  type ColumnId,
  type Filters,
  loadColumns,
  matches,
  parseFilters,
  PERIOD_LABEL,
  type PeriodKey,
  periodNumbers,
  placementKey,
  RESET_PATCH,
  saveColumns,
  sortRows,
  STATUS_KEYS,
  STATUS_LABEL,
  statusKey,
  writeFilters,
} from "@/components/ads-list/model";
import { BulkDialog } from "@/components/bulk-dialog";
import { EmptyState } from "@/components/empty-state";
import { useAccountCtx } from "@/components/layout/account-layout";
import { QueryError } from "@/components/query-error";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { headerState, toggleAll, toggleOne } from "@/lib/bulk";
import { enDate, enMoney, enNum, enPct } from "@/lib/format-en";
import { useAds, useTexts } from "@/lib/queries";
import type { AdRow, BulkKind, TextVariant } from "@/lib/types";
import { cn } from "@/lib/utils";

const STATUS_VARIANT = {
  active: "success",
  on_hold: "warning",
  in_review: "info",
  declined: "destructive",
  stopped: "secondary",
} as const;

const PLACEMENT_LABEL = { channels: "Channel", bots: "Bot", search: "Search" } as const;

/** The ads list, styled and worded like ads.telegram.org (English, left to right); the rest of the panel stays Persian. */
export default function AdsPage() {
  const account = useAccountCtx();
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const f = useMemo(() => parseFilters(params), [params]);
  const { data, isLoading, error, refetch } = useAds(account.slug, {
    period: f.period,
    from: f.from,
    to: f.to,
    archived: f.archived,
  });
  const texts = useTexts(account.slug);
  const cur = account.telegram.currency || "TON";
  const [columns, setColumns] = useState<ColumnId[]>(loadColumns);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [bulk, setBulk] = useState<BulkKind | null>(null);
  const autoOpened = useRef(false);
  const variantText = f.variant ? texts.data?.find((t) => String(t.id) === f.variant)?.text : undefined;

  const set = (patch: Partial<Filters>) => setParams(writeFilters(params, patch), { replace: true });

  const rows = useMemo(
    () => sortRows((data?.ads ?? []).filter((a) => matches(a, f, variantText)), f.sort, f.dir),
    [data, f, variantText],
  );
  const visibleIds = useMemo(() => rows.map((a) => a.ad_id), [rows]);
  const chosen = useMemo(() => (data?.ads ?? []).filter((a) => selected.has(a.ad_id)), [data, selected]);
  const shownColumns = COLUMNS.filter((c) => columns.includes(c.id));

  // Links from other pages (?referral=without&select=all&bulk=referral) select every result and open the dialog.
  useEffect(() => {
    if (autoOpened.current || !data || !visibleIds.length || params.get("select") !== "all") return;
    autoOpened.current = true;
    setSelected(new Set(visibleIds));
    const k = params.get("bulk");
    if (k === "referral" || k === "content") setBulk(k);
  }, [data, visibleIds, params]);

  const toggleColumn = (id: ColumnId) => {
    const next = columns.includes(id) ? columns.filter((c) => c !== id) : [...columns, id];
    setColumns(next);
    saveColumns(next);
  };
  const sortBy = (id: string) =>
    set(f.sort === id ? { dir: f.dir === "desc" ? "asc" : "desc" } : { sort: id, dir: id === "ad" ? "asc" : "desc" });
  const filterCount = activeFilterCount(f);

  return (
    <div dir="ltr" lang="en" className="space-y-4 text-left" data-testid="ads-list">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0 space-y-1">
          <h1 className="text-xl font-bold tracking-tight sm:text-2xl">Ads</h1>
          <p className="text-sm text-muted-foreground">All ads of this account with statistics, referral tags and sales.</p>
        </div>
        <Button asChild>
          <Link to="new">
            <PlusIcon /> New Ad
          </Link>
        </Button>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-0 flex-1 basis-56">
          <SearchIcon className="pointer-events-none absolute start-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search title, text, target or ad ID"
            className="ps-8"
            value={f.q}
            onChange={(e) => set({ q: e.target.value })}
            aria-label="Search"
          />
        </div>
        <Select dir="ltr" value={f.period} onValueChange={(v) => set({ period: v as PeriodKey })}>
          <SelectTrigger className="w-40" aria-label="Period">
            <SelectValue />
          </SelectTrigger>
          <SelectContent dir="ltr">
            {(Object.keys(PERIOD_LABEL) as PeriodKey[]).map((k) => (
              <SelectItem key={k} value={k}>
                {PERIOD_LABEL[k]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {f.period === "custom" && (
          <div className="flex items-center gap-1">
            <Input type="date" className="w-36" value={f.from} onChange={(e) => set({ from: e.target.value })} aria-label="From" />
            <span className="text-muted-foreground">–</span>
            <Input type="date" className="w-36" value={f.to} onChange={(e) => set({ to: e.target.value })} aria-label="To" />
          </div>
        )}
        <div className="hidden items-center gap-2 md:flex">
          <StatusMenu value={f.status} onChange={(status) => set({ status })} />
          <Select dir="ltr" value={f.placement || "all"} onValueChange={(v) => set({ placement: v === "all" ? "" : (v as Filters["placement"]) })}>
            <SelectTrigger className="w-36" aria-label="Placement">
              <SelectValue />
            </SelectTrigger>
            <SelectContent dir="ltr">
              <SelectItem value="all">All placements</SelectItem>
              <SelectItem value="channels">Channels</SelectItem>
              <SelectItem value="bots">Bots</SelectItem>
              <SelectItem value="search">Search</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <FiltersSheet f={f} set={set} texts={texts.data ?? []} count={filterCount} />
        <DropdownMenu dir="ltr">
          <DropdownMenuTrigger asChild>
            <Button variant="outline" aria-label="Columns">
              <ColumnsIcon /> <span className="hidden sm:inline">Columns</span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <div dir="ltr">
            <DropdownMenuLabel>Telegram Ads</DropdownMenuLabel>
            {COLUMNS.filter((c) => !c.extra).map((c) => (
              <DropdownMenuCheckboxItem key={c.id} checked={columns.includes(c.id)} onCheckedChange={() => toggleColumn(c.id)} onSelect={(e) => e.preventDefault()}>
                {c.label}
              </DropdownMenuCheckboxItem>
            ))}
            <DropdownMenuSeparator />
            <DropdownMenuLabel>This panel</DropdownMenuLabel>
            {COLUMNS.filter((c) => c.extra).map((c) => (
              <DropdownMenuCheckboxItem key={c.id} checked={columns.includes(c.id)} onCheckedChange={() => toggleColumn(c.id)} onSelect={(e) => e.preventDefault()}>
                {c.label}
              </DropdownMenuCheckboxItem>
            ))}
            </div>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-muted-foreground" data-testid="result-count">
          {enNum(rows.length)} {rows.length === 1 ? "ad" : "ads"}
          {data && rows.length !== data.ads.length ? ` of ${enNum(data.ads.length)}` : ""}
          {data?.period ? ` · ${PERIOD_LABEL[(data.period.preset as PeriodKey) ?? "7d"] ?? data.period.preset}` : ""}
          {data?.period?.preset === "today" ? " (since 00:00 local time)" : ""}
        </span>
        {filterCount > 0 && (
          <Button size="sm" variant="ghost" onClick={() => set(RESET_PATCH)}>
            <RotateCcwIcon /> Reset filters
          </Button>
        )}
        {!f.archived && (data?.archived_count ?? 0) > 0 && (
          <Button size="sm" variant="ghost" onClick={() => set({ archived: true })}>
            <ArchiveIcon /> Archived ({enNum(data?.archived_count)})
          </Button>
        )}
        {f.archived && (
          <Badge variant="secondary">
            <ArchiveIcon /> Showing archived ads
          </Badge>
        )}
        {rows.length > 0 && !f.archived && (
          <Button size="sm" variant="outline" className="ms-auto" onClick={() => setSelected(new Set(visibleIds))}>
            <ListChecksIcon /> Select all {enNum(visibleIds.length)} results
          </Button>
        )}
      </div>

      {error && <QueryError error={error} retry={() => refetch()} />}
      {isLoading && <Skeleton className="h-96" />}
      {!isLoading && !error && rows.length === 0 && (
        <EmptyState
          icon={MegaphoneIcon}
          title={data?.ads.length ? "No ads match these filters" : "No ads yet"}
          description={data?.ads.length ? undefined : "Ads appear here after the first sync."}
        />
      )}

      {rows.length > 0 && (
        <>
          <Card className="hidden py-0 md:flex">
            <Table>
              <TableHeader>
                <TableRow>
                  {!f.archived && (
                    <TableHead className="w-8">
                      <Checkbox
                        aria-label="Select all"
                        checked={headerState(selected, visibleIds)}
                        onCheckedChange={() => setSelected((x) => toggleAll(x, visibleIds))}
                      />
                    </TableHead>
                  )}
                  <SortHead id="ad" label="Ad" f={f} onSort={sortBy} />
                  {shownColumns.map((c) => (
                    <SortHead key={c.id} id={c.id} label={c.label} f={f} onSort={sortBy} numeric={c.numeric && c.id !== "added"} />
                  ))}
                  <TableHead className="w-10" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((a) => (
                  <TableRow
                    key={a.ad_id}
                    className="cursor-pointer"
                    data-state={selected.has(a.ad_id) ? "selected" : undefined}
                    onClick={(e) => {
                      if ((e.target as HTMLElement).closest("button,a,[role=menuitem],[role=checkbox]")) return;
                      nav(String(a.ad_id));
                    }}
                  >
                    {!f.archived && (
                      <TableCell>
                        <Checkbox
                          aria-label={`Select ad ${a.ad_id}`}
                          checked={selected.has(a.ad_id)}
                          onCheckedChange={() => setSelected((x) => toggleOne(x, a.ad_id))}
                        />
                      </TableCell>
                    )}
                    <TableCell className="max-w-[20rem] min-w-[14rem] whitespace-normal">
                      <div className="flex items-center gap-1.5">
                        <Link to={String(a.ad_id)} className="truncate font-medium hover:underline">
                          {a.title || a.target_label || `#${a.ad_id}`}
                        </Link>
                        <Badge variant="outline" className="shrink-0 text-[10px]">
                          {PLACEMENT_LABEL[placementKey(a)]}
                        </Badge>
                      </div>
                      <div dir="auto" className="line-clamp-1 text-xs text-muted-foreground wrap-anywhere">
                        {a.text}
                      </div>
                    </TableCell>
                    {shownColumns.map((c) => (
                      <TableCell key={c.id} className={cn(c.numeric && c.id !== "added" && "text-right tabular-nums", "whitespace-nowrap")}>
                        <Cell id={c.id} a={a} cur={cur} />
                      </TableCell>
                    ))}
                    <TableCell>
                      <div dir="rtl">
                        <AdActions account={account} ad={a} compact />
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Card>

          <div className="grid gap-2 md:hidden">
            {rows.map((a) => {
              const n = periodNumbers(a);
              return (
                <Card key={a.ad_id} className={cn("gap-3 px-4 py-3", selected.has(a.ad_id) && "border-primary")}>
                  <div className="flex items-start gap-2">
                    {!f.archived && (
                      <Checkbox
                        className="mt-1"
                        aria-label={`Select ad ${a.ad_id}`}
                        checked={selected.has(a.ad_id)}
                        onCheckedChange={() => setSelected((x) => toggleOne(x, a.ad_id))}
                      />
                    )}
                    <Link to={String(a.ad_id)} className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        <span className="truncate font-medium">{a.title || a.target_label || `#${a.ad_id}`}</span>
                        <Badge variant="outline" className="shrink-0 text-[10px]">
                          {PLACEMENT_LABEL[placementKey(a)]}
                        </Badge>
                      </div>
                      <div dir="auto" className="line-clamp-2 text-xs text-muted-foreground wrap-anywhere">
                        {a.text}
                      </div>
                    </Link>
                    <div dir="rtl">
                      <AdActions account={account} ad={a} compact />
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <StatusBadge a={a} />
                    {a.remaining <= 0.01 && <Badge variant="warning">Out of budget{a.empty_days != null ? ` · ${a.empty_days}d` : ""}</Badge>}
                    {a.tag_name && (
                      <Badge variant="info" className="max-w-full">
                        <TagIcon /> <span className="truncate">{a.tag_name}</span>
                      </Badge>
                    )}
                  </div>
                  <div className="grid grid-cols-4 gap-2 text-center text-xs">
                    <Mini label="Spent" value={enMoney(n.spent)} />
                    <Mini label="Actions" value={enNum(n.actions)} />
                    <Mini label="CPA" value={enMoney(n.actions ? n.spent / n.actions : null)} />
                    <Mini label={`Budget ${cur}`} value={enMoney(a.remaining)} />
                  </div>
                </Card>
              );
            })}
          </div>
        </>
      )}

      {selected.size > 0 && !f.archived && (
        <>
          <div className="h-20 md:hidden" aria-hidden />
          <div
            role="toolbar"
            aria-label="Bulk actions"
            dir="ltr"
            className="fixed inset-x-0 bottom-0 z-40 border-t bg-background/95 px-4 py-3 shadow-lg backdrop-blur md:sticky md:bottom-4 md:mx-auto md:max-w-3xl md:rounded-xl md:border"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-medium tabular-nums">{enNum(selected.size)} selected</span>
              <div className="ms-auto flex flex-wrap gap-2">
                <Button size="sm" onClick={() => setBulk("content")}>
                  <PencilLineIcon /> Text / Photo
                </Button>
                <Button size="sm" variant="outline" onClick={() => setBulk("referral")}>
                  <LinkIcon /> Referral link
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())} aria-label="Clear selection">
                  <XIcon />
                </Button>
              </div>
            </div>
          </div>
        </>
      )}
      {bulk && chosen.length > 0 && (
        <div dir="rtl" lang="fa">
          <BulkDialog
            account={account}
            kind={bulk}
            targets={chosen.map((a) => ({
              ad_id: a.ad_id,
              label: a.target_label || a.title || `#${a.ad_id}`,
              text: a.text,
              placement: a.placement,
              target_type: a.target_type,
            }))}
            onClose={() => setBulk(null)}
          />
        </div>
      )}
    </div>
  );
}

function StatusBadge({ a }: { a: AdRow }) {
  const k = statusKey(a);
  return <Badge variant={STATUS_VARIANT[k]}>{STATUS_LABEL[k]}</Badge>;
}

function Cell({ id, a, cur }: { id: ColumnId; a: AdRow; cur: string }) {
  const n = periodNumbers(a);
  switch (id) {
    case "status":
      return <StatusBadge a={a} />;
    case "cpm":
      return <>{enMoney(a.cpm, cur)}</>;
    case "views":
      return <>{enNum(n.views)}</>;
    case "clicks":
      return <>{enNum(n.clicks)}</>;
    case "actions":
      return <>{enNum(n.actions)}</>;
    case "ctr":
      return <>{enPct(n.views ? n.clicks / n.views : null)}</>;
    case "cvr":
      return <>{enPct(n.clicks ? n.actions / n.clicks : null)}</>;
    case "cpc":
      return <>{enMoney(n.clicks ? n.spent / n.clicks : null, cur)}</>;
    case "cpa":
      return <>{enMoney(n.actions ? n.spent / n.actions : null, cur)}</>;
    case "spent":
      return <>{enMoney(n.spent, cur)}</>;
    case "budget":
      return <>{enMoney(a.remaining, cur)}</>;
    case "target":
      return <span className="inline-block max-w-[12rem] truncate align-bottom">{a.target_label || "—"}</span>;
    case "added":
      return <>{enDate(a.created_date)}</>;
    case "buyers":
      return <>{a.buyers == null ? "—" : enNum(a.buyers)}</>;
    case "cpp":
      return <>{enMoney(a.cpp, cur)}</>;
    case "referral":
      return a.tag_name ? (
        <span className="inline-flex max-w-[10rem] items-center gap-1 text-info">
          <TagIcon className="size-3 shrink-0" />
          <span className="truncate">{a.tag_name}</span>
          {a.tag_shared && <span className="text-muted-foreground">(shared)</span>}
        </span>
      ) : (
        <span className="text-muted-foreground">—</span>
      );
    case "empty":
      return a.remaining > 0.01 ? (
        <span className="text-muted-foreground">—</span>
      ) : (
        <span className="text-warning">{a.empty_days == null ? "Out of budget" : `${a.empty_days} ${a.empty_days === 1 ? "day" : "days"}`}</span>
      );
  }
}

function SortHead({
  id,
  label,
  f,
  onSort,
  numeric,
}: {
  id: string;
  label: string;
  f: Filters;
  onSort: (id: string) => void;
  numeric?: boolean;
}) {
  const active = f.sort === id;
  const Icon = !active ? ArrowUpDownIcon : f.dir === "asc" ? ArrowUpIcon : ArrowDownIcon;
  return (
    <TableHead className={cn(numeric && "text-right")} aria-sort={active ? (f.dir === "asc" ? "ascending" : "descending") : "none"}>
      <button
        type="button"
        onClick={() => onSort(id)}
        className={cn("inline-flex items-center gap-1 whitespace-nowrap hover:text-foreground", active && "text-foreground")}
      >
        {label}
        <Icon className={cn("size-3", !active && "opacity-40")} />
      </button>
    </TableHead>
  );
}

function StatusMenu({ value, onChange }: { value: Filters["status"]; onChange: (v: Filters["status"]) => void }) {
  return (
    <DropdownMenu dir="ltr">
      <DropdownMenuTrigger asChild>
        <Button variant="outline" aria-label="Status">
          Status{value.length ? ` (${value.length})` : ""}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent>
        <div dir="ltr">
        {STATUS_KEYS.map((k) => (
          <DropdownMenuCheckboxItem
            key={k}
            checked={value.includes(k)}
            onSelect={(e) => e.preventDefault()}
            onCheckedChange={(on) => onChange(on ? [...value, k] : value.filter((x) => x !== k))}
          >
            {STATUS_LABEL[k]}
          </DropdownMenuCheckboxItem>
        ))}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function FiltersSheet({
  f,
  set,
  texts,
  count,
}: {
  f: Filters;
  set: (p: Partial<Filters>) => void;
  texts: TextVariant[];
  count: number;
}) {
  return (
    <Sheet>
      <SheetTrigger asChild>
        <Button variant="outline" aria-label="Filters">
          <SlidersHorizontalIcon /> Filters{count ? ` (${count})` : ""}
        </Button>
      </SheetTrigger>
      <SheetContent side="end" dir="ltr" lang="en" className="w-[90%] max-w-sm overflow-y-auto p-5 text-left">
        <SheetHeader className="space-y-1">
          <SheetTitle>Filters</SheetTitle>
          <SheetDescription>All filters combine and are kept in the page address.</SheetDescription>
        </SheetHeader>
        <div className="grid gap-4 text-sm">
          <div className="grid gap-1.5">
            <Label>Status</Label>
            <div className="flex flex-wrap gap-1.5">
              {STATUS_KEYS.map((k) => {
                const on = f.status.includes(k);
                return (
                  <button
                    key={k}
                    type="button"
                    aria-pressed={on}
                    onClick={() => set({ status: on ? f.status.filter((x) => x !== k) : [...f.status, k] })}
                    className={cn("rounded-md border px-2 py-1 text-xs", on && "border-primary bg-accent text-accent-foreground")}
                  >
                    {STATUS_LABEL[k]}
                  </button>
                );
              })}
            </div>
          </div>
          <Choice
            label="Placement"
            value={f.placement}
            options={[["", "All"], ["channels", "Channels"], ["bots", "Bots"], ["search", "Search"]]}
            onChange={(v) => set({ placement: v as Filters["placement"] })}
          />
          <Range label="CPM" min={f.cpmMin} max={f.cpmMax} onChange={(cpmMin, cpmMax) => set({ cpmMin, cpmMax })} />
          <Range label="Budget" min={f.budgetMin} max={f.budgetMax} onChange={(budgetMin, budgetMax) => set({ budgetMin, budgetMax })} />
          <Choice
            label="Budget left"
            value={f.budget}
            options={[["", "Any"], ["has", "Has budget"], ["out", "Out of budget"]]}
            onChange={(v) => set({ budget: v as Filters["budget"] })}
          />
          <Choice
            label="Target type"
            value={f.target}
            options={[["", "Any"], ["channel", "Channel"], ["bot", "Bot"]]}
            onChange={(v) => set({ target: v as Filters["target"] })}
          />
          <Choice
            label="Referral link"
            value={f.referral}
            options={[["", "Any"], ["with", "With referral"], ["without", "Without referral"]]}
            onChange={(v) => set({ referral: v as Filters["referral"] })}
          />
          <div className="grid gap-1.5">
            <Label htmlFor="flt-variant">Text variant</Label>
            <Select dir="ltr" value={f.variant || "all"} onValueChange={(v) => set({ variant: v === "all" ? "" : v })}>
              <SelectTrigger id="flt-variant" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent dir="ltr">
                <SelectItem value="all">Any text</SelectItem>
                {texts.map((t) => (
                  <SelectItem key={t.id} value={String(t.id)}>
                    <span dir="auto">{t.name}</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-center justify-between gap-2">
            <Label htmlFor="flt-photo">Has photo</Label>
            <Switch id="flt-photo" checked={f.photo} onCheckedChange={(photo) => set({ photo })} />
          </div>
          <div className="grid gap-1.5">
            <Label>Date added</Label>
            <div className="flex items-center gap-1">
              <Input type="date" value={f.addedFrom} onChange={(e) => set({ addedFrom: e.target.value })} aria-label="Added from" />
              <span className="text-muted-foreground">–</span>
              <Input type="date" value={f.addedTo} onChange={(e) => set({ addedTo: e.target.value })} aria-label="Added to" />
            </div>
          </div>
          <div className="flex items-center justify-between gap-2">
            <Label htmlFor="flt-archived">Show archived ads</Label>
            <Switch id="flt-archived" checked={f.archived} onCheckedChange={(archived) => set({ archived })} />
          </div>
          <Button variant="outline" onClick={() => set(RESET_PATCH)}>
            <RotateCcwIcon /> Reset filters
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}

function Choice({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: [string, string][];
  onChange: (v: string) => void;
}) {
  return (
    <div className="grid gap-1.5" role="radiogroup" aria-label={label}>
      <Label>{label}</Label>
      <div className="flex flex-wrap gap-1.5">
        {options.map(([v, l]) => (
          <button
            key={v || "any"}
            type="button"
            role="radio"
            aria-checked={value === v}
            onClick={() => onChange(v)}
            className={cn("rounded-md border px-2 py-1 text-xs", value === v && "border-primary bg-accent text-accent-foreground")}
          >
            {l}
          </button>
        ))}
      </div>
    </div>
  );
}

function Range({ label, min, max, onChange }: { label: string; min: string; max: string; onChange: (min: string, max: string) => void }) {
  return (
    <div className="grid gap-1.5">
      <Label>{label}</Label>
      <div className="flex items-center gap-1">
        <Input type="number" inputMode="decimal" step="0.01" placeholder="Min" value={min} onChange={(e) => onChange(e.target.value, max)} aria-label={`${label} min`} />
        <span className="text-muted-foreground">–</span>
        <Input type="number" inputMode="decimal" step="0.01" placeholder="Max" value={max} onChange={(e) => onChange(min, e.target.value)} aria-label={`${label} max`} />
      </div>
    </div>
  );
}

function Mini({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-muted/50 px-1 py-1.5">
      <div className="truncate font-semibold tabular-nums">{value}</div>
      <div className="truncate text-muted-foreground">{label}</div>
    </div>
  );
}
