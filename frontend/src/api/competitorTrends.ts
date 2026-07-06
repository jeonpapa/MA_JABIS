import { api } from './client';

/** B1: 클러스터 멤버 기사 (competitor_news.trend_id 역링크) */
export interface TrendSource {
  name: string | null;
  url: string | null;
  tier: number | null;
  pub_date: string | null;
}

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
  /** 'manual' | 'auto_naver' | 'promoted' */
  source_type?: string;
  importance?: string | null;
  /** B2: 대표(최저=최고신뢰) 매체 tier — 1 전문지 / 2 종합 / 3 미등록 */
  source_tier?: number | null;
  /** B1: 같은 이벤트를 다룬 기사 목록 (tier ASC, 최신순) */
  sources?: TrendSource[];
  /** 매체 수 — sources 없으면 primary url 존재 시 1 */
  source_count?: number;
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
