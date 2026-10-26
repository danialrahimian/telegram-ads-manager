/** TanStack Query hooks for every backend endpoint. Account data lives under ["acc", slug, ...]. */
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryKey,
} from "@tanstack/react-query";
import { toast } from "sonner";
import { api, errorMessage } from "@/lib/api";
import type {
  Account,
  AdDetail,
  AdRow,
  AdsPeriod,
  Analysis,
  AppSettings,
  BulkOperation,
  Capabilities,
  DailyRow,
  DiagReport,
  EventRow,
  Job,
  Live,
  Me,
  OverviewAccount,
  Proposal,
  SalesOverview,
  Summary,
  SystemInfo,
  Tag,
  TextVariant,
  TrackingInfo,
} from "@/lib/types";

export const keys = {
  me: ["me"] as const,
  settings: ["settings"] as const,
  system: ["system"] as const,
  overview: ["overview"] as const,
  accounts: ["accounts"] as const,
  events: ["events"] as const,
  acc: (slug: string) => ["acc", slug] as const,
};

export function useMe() {
  return useQuery({ queryKey: keys.me, queryFn: () => api.get<Me>("/auth/me"), staleTime: 60_000, retry: 1 });
}

export function useAppSettings() {
  return useQuery({ queryKey: keys.settings, queryFn: () => api.get<AppSettings>("/settings"), staleTime: 30_000 });
}

/** Dashboard polling interval (ms), set in the global settings (default 5s). */
export function useRefreshMs(): number {
  const { data } = useAppSettings();
  return Math.max(2, data?.ui_refresh_seconds ?? 5) * 1000;
}

export function useSystem() {
  return useQuery({ queryKey: keys.system, queryFn: () => api.get<SystemInfo>("/system"), staleTime: 60_000 });
}

export function useOverview() {
  const refetchInterval = useRefreshMs();
  return useQuery({
    queryKey: keys.overview,
    queryFn: () => api.get<{ accounts: OverviewAccount[] }>("/overview"),
    refetchInterval,
  });
}

export function useAccounts() {
  const refetchInterval = useRefreshMs();
  return useQuery({
    queryKey: keys.accounts,
    queryFn: () => api.get<{ accounts: Account[] }>("/accounts"),
    select: (d) => d.accounts,
    refetchInterval,
  });
}

export function useEvents() {
  return useQuery({ queryKey: keys.events, queryFn: () => api.get<{ events: EventRow[] }>("/events?limit=200") });
}

const A = (slug: string) => `/accounts/${encodeURIComponent(slug)}`;

export function useAccount(slug: string) {
  const refetchInterval = useRefreshMs();
  return useQuery({
    queryKey: [...keys.acc(slug), "info"],
    queryFn: () => api.get<Account>(A(slug)),
    refetchInterval,
    retry: (n, e) => !(e && "status" in e && (e as { status: number }).status === 404) && n < 2,
  });
}

export function useLive(slug: string) {
  const refetchInterval = useRefreshMs();
  return useQuery({
    queryKey: [...keys.acc(slug), "live"],
    queryFn: () => api.get<{ account: Account; live: Live; ui_refresh_seconds: number }>(`${A(slug)}/live`),
    refetchInterval,
  });
}

export function useDashboard(slug: string, days = 60) {
  const refetchInterval = useRefreshMs();
  return useQuery({
    queryKey: [...keys.acc(slug), "dashboard", days],
    queryFn: () => api.get<{ summary: Summary; daily: DailyRow[] }>(`${A(slug)}/dashboard?days=${days}`),
    refetchInterval: refetchInterval * 3,
    placeholderData: keepPreviousData,
  });
}

export interface AdsQuery {
  since?: string;
  archived?: boolean;
  period?: string;
  from?: string;
  to?: string;
}

export function useAds(slug: string, opts: AdsQuery = {}) {
  const refetchInterval = useRefreshMs();
  const qs = new URLSearchParams();
  if (opts.since) qs.set("since", opts.since);
  if (opts.archived) qs.set("archived", "true");
  if (opts.period) qs.set("period", opts.period);
  if (opts.period === "custom" && opts.from) qs.set("from", opts.from);
  if (opts.period === "custom" && opts.to) qs.set("to", opts.to);
  const q = qs.toString();
  return useQuery({
    queryKey: [...keys.acc(slug), "ads", q],
    queryFn: () =>
      api.get<{ ads: AdRow[]; since: string | null; archived_count: number; period: AdsPeriod }>(
        `${A(slug)}/ads${q ? `?${q}` : ""}`,
      ),
    refetchInterval: refetchInterval * 2,
    placeholderData: keepPreviousData,
  });
}

export function useAd(slug: string, adId: number) {
  const refetchInterval = useRefreshMs();
  return useQuery({
    queryKey: [...keys.acc(slug), "ad", adId],
    queryFn: () => api.get<AdDetail>(`${A(slug)}/ads/${adId}`),
    refetchInterval: refetchInterval * 2,
  });
}

export function useProposals(slug: string) {
  const refetchInterval = useRefreshMs();
  return useQuery({
    queryKey: [...keys.acc(slug), "proposals"],
    queryFn: () =>
      api.get<{
        pending: Proposal[];
        history: Proposal[];
        topup_used_24h: number;
        topup_daily_cap: number;
        labels: Record<string, string>;
      }>(`${A(slug)}/proposals`),
    refetchInterval: refetchInterval * 2,
  });
}

export function useTexts(slug: string) {
  return useQuery({
    queryKey: [...keys.acc(slug), "texts"],
    queryFn: () => api.get<{ texts: TextVariant[] }>(`${A(slug)}/texts`),
    select: (d) => d.texts,
  });
}

export function useTags(slug: string) {
  return useQuery({
    queryKey: [...keys.acc(slug), "tags"],
    queryFn: () => api.get<{ tags: Tag[] }>(`${A(slug)}/tags`),
    select: (d) => d.tags,
  });
}

export function useSales(slug: string, days: number) {
  const refetchInterval = useRefreshMs();
  return useQuery({
    queryKey: [...keys.acc(slug), "sales", days],
    queryFn: () => api.get<{ overview: SalesOverview; tags: Tag[] }>(`${A(slug)}/sales?days=${days}`),
    refetchInterval: refetchInterval * 3,
    placeholderData: keepPreviousData,
  });
}

export function useAnalyses(slug: string) {
  return useQuery({
    queryKey: [...keys.acc(slug), "analysis"],
    queryFn: () => api.get<{ analyses: Analysis[] }>(`${A(slug)}/analysis`),
    select: (d) => d.analyses,
  });
}

export function useActivity(slug: string) {
  const refetchInterval = useRefreshMs();
  return useQuery({
    queryKey: [...keys.acc(slug), "activity"],
    queryFn: () => api.get<{ now: number; jobs: Job[]; events: EventRow[] }>(`${A(slug)}/activity`),
    refetchInterval,
  });
}

export function useBulkOps(slug: string) {
  const refetchInterval = useRefreshMs();
  return useQuery({
    queryKey: [...keys.acc(slug), "bulk"],
    queryFn: () => api.get<{ operations: BulkOperation[]; capabilities: Capabilities }>(`${A(slug)}/bulk`),
    refetchInterval,
  });
}

export function useBulkOp(slug: string, id: number) {
  const refetchInterval = useRefreshMs();
  return useQuery({
    queryKey: [...keys.acc(slug), "bulk", id],
    queryFn: () => api.get<BulkOperation>(`${A(slug)}/bulk/${id}`),
    refetchInterval,
  });
}

export function useCapabilities(slug: string) {
  return useQuery({
    queryKey: [...keys.acc(slug), "capabilities"],
    queryFn: () => api.get<Capabilities>(`${A(slug)}/bulk/capabilities`),
  });
}

export function useTracking(slug: string) {
  return useQuery({
    queryKey: [...keys.acc(slug), "tracking"],
    queryFn: () => api.get<TrackingInfo>(`${A(slug)}/tracking`),
  });
}

export function useBotDiag(slug: string) {
  return useQuery({
    queryKey: [...keys.acc(slug), "bot-diag"],
    queryFn: () => api.get<{ report: DiagReport | null; text: string }>(`${A(slug)}/bot/diagnose`),
  });
}

// ------------------------------------------------------------------ mutations

interface ActionOptions<TVars, TRes> {
  fn: (vars: TVars) => Promise<TRes>;
  invalidate?: QueryKey[];
  success?: string | ((res: TRes, vars: TVars) => string | null);
  onSuccess?: (res: TRes, vars: TVars) => void;
  silentError?: boolean;
}

/** useMutation + toast + cache invalidation, the pattern every button in the app uses. */
export function useAction<TVars = void, TRes = unknown>(opts: ActionOptions<TVars, TRes>) {
  const qc = useQueryClient();
  return useMutation<TRes, Error, TVars>({
    mutationFn: opts.fn,
    onSuccess: async (res, vars) => {
      const msg =
        typeof opts.success === "function"
          ? opts.success(res, vars)
          : (opts.success ?? (res as { message?: string } | undefined)?.message ?? null);
      if (msg) toast.success(msg);
      opts.onSuccess?.(res, vars);
      await Promise.all((opts.invalidate ?? []).map((k) => qc.invalidateQueries({ queryKey: k })));
    },
    onError: (e) => {
      if (!opts.silentError) toast.error(errorMessage(e));
    },
  });
}

export const accountApi = (slug: string) => A(slug);
