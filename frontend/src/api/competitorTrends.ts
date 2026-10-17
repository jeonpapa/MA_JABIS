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
  /** 품질 가드(읽기 시점) — 원래 badge/회사, 주어 브랜드, low-confidence 플래그 */
  quality?: TrendQuality;
  /** 같은 사건 묶음 대표 카드 id (단독이면 자기 id) */
  group_id?: number;
  /** 묶음 카드 수 (단독 1) */
  group_size?: number;
  /** 대표 카드에만 — 같은 사건을 다룬 나머지 카드 */
  related?: RelatedTrend[];
}

export interface TrendQuality {
  brand?: string | null;
  flags?: string[];
  low_confidence?: boolean;
  badge_original?: string;
  badge_reason?: string | null;
  company_original?: string;
  company_reason?: string | null;
}

export interface RelatedTrend {
  id: number;
  headline: string;
  date: string;
  source: string | null;
  url: string | null;
  company: string;
  badge: string;
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
  '해외 승인',
  '급여 추진',
] as const;

/** badge 정의 — 필터 칩 툴팁 */
export const COMPETITOR_BADGE_HELP: Record<string, string> = {
  '신규 출시': '국내 허가·출시',
  '가격 변동': '약가 인하/인상 · 약가(협상) 타결 · 상한금액 변경',
  '임상 진행': '임상 결과·진행',
  '급여 등재': '국내 급여 등재/확대 확정·시행',
  '파이프라인': '파이프라인 신규 단계',
  '전략 변화': '제휴·가이드라인·전략',
  '해외 승인': 'FDA·EMA·NICE 등 해외 허가·급여 결정 (국내 출시 아님)',
  '급여 추진': '급여 신청·심의·협상 중 (미확정)',
};

/** raw=true → 품질 가드/묶음 미적용 원본 (admin 편집용) */
export async function listCompetitorTrends(opts: { raw?: boolean } = {}): Promise<CompetitorTrend[]> {
  const r = await api.get<{ items: CompetitorTrend[] }>(
    opts.raw ? '/api/competitor-trends?raw=1' : '/api/competitor-trends',
  );
  return r.items;
}

/** 묶음 대표 카드만 (같은 사건 중복 제거). group_id 없으면(구 API) 그대로 */
export function primaryTrends(items: CompetitorTrend[]): CompetitorTrend[] {
  return items.filter(i => i.group_id == null || i.group_id === i.id);
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
