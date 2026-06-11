import { api } from './client';

export interface CompetitorTrend {
  id: number;
  company: string;
  logo: string | null;
  color: string | null;
  badge: string;
  badgeColor: string | null;
  headline: string;
  detail: string;
  date: string;
  source: string | null;
  url: string | null;
  created_at: string;
  updated_at: string;
}

export interface CompetitorTrendInput {
  company: string;
  logo?: string | null;
  color?: string | null;
  badge: string;
  badgeColor?: string | null;
  headline: string;
  detail: string;
  date: string;
  source?: string | null;
  url?: string | null;
}

export const COMPETITOR_BADGES = [
  '신규 출시',
  '가격 변동',
  '임상 진행',
  '급여 등재',
  '파이프라인',
  '전략 변화',
] as const;

export async function listCompetitorTrends(): Promise<CompetitorTrend[]> {
  const r = await api.get<{ items: CompetitorTrend[] }>('/api/competitor-trends');
  return r.items;
}

export async function createCompetitorTrend(input: CompetitorTrendInput): Promise<CompetitorTrend> {
  const r = await api.post<{ item: CompetitorTrend }>('/api/admin/competitor-trends', input);
  return r.item;
}

export async function updateCompetitorTrend(
  id: number,
  patch: Partial<CompetitorTrendInput>,
): Promise<CompetitorTrend> {
  const r = await api.patch<{ item: CompetitorTrend }>(`/api/admin/competitor-trends/${id}`, patch);
  return r.item;
}

export async function deleteCompetitorTrend(id: number): Promise<void> {
  await api.delete<{ ok: true }>(`/api/admin/competitor-trends/${id}`);
}

export interface CompetitorRefreshResult {
  ok: boolean;
  dry_run: boolean;
  days: number;
  model: string;
  brands: Array<{
    brand: string;
    company: string;
    fetched: number;
    accepted: number;
    skipped_low: number;
    upserted: number;
    errors: string[];
  }>;
  totals: { fetched: number; accepted: number; upserted: number };
  error?: string;
}

export async function refreshCompetitorTrends(opts: {
  days?: number; dry_run?: boolean; model?: string;
} = {}): Promise<CompetitorRefreshResult> {
  return api.post<CompetitorRefreshResult>('/api/admin/competitor-trends/refresh', opts);
}
