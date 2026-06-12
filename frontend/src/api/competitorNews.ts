import { api } from './client';

// Tier-1 전문지 뉴스 아카이브 (메타데이터 + 링크 전용, 1년 보존)
// 백엔드: GET /api/competitor-news, GET /api/competitor-news/brands

export interface CompetitorNewsItem {
  id: number;
  brand: string;
  company: string;
  anchor: string;
  kind: 'competitor' | 'msd_asset';
  title: string;
  url: string;
  naver_link: string | null;
  source_domain: string;
  source_name: string | null; // 한글 매체명 (예: '데일리팜')
  tier: number;
  description: string; // Naver 발췌 (HTML 제거됨)
  pub_date: string; // 'YYYY-MM-DD'
  trend_id: number | null;
  fetched_at: string;
  expires_at: string; // 발행일 + 1년
}

export interface NewsBrand {
  query: string;
  company: string;
  anchor: string;
  kind: 'competitor' | 'msd_asset';
  logo: string | null;
  color: string | null;
  news_count: number;
}

export interface NewsBySource {
  source_name: string | null;
  source_domain: string;
  n: number;
}

export interface NewsBrandsResponse {
  brands: NewsBrand[];
  stats: {
    total: number;
    by_source: NewsBySource[];
    earliest: string | null;
    latest: string | null;
  };
}

export interface CompetitorNewsParams {
  brand?: string;
  company?: string;
  tier?: number;
  days?: number;
  limit?: number;
}

export async function fetchCompetitorNews(
  params: CompetitorNewsParams = {},
): Promise<CompetitorNewsItem[]> {
  const qs = new URLSearchParams();
  if (params.brand) qs.set('brand', params.brand);
  if (params.company) qs.set('company', params.company);
  if (params.tier != null) qs.set('tier', String(params.tier));
  if (params.days != null) qs.set('days', String(params.days));
  if (params.limit != null) qs.set('limit', String(params.limit));
  const q = qs.toString();
  const r = await api.get<{ items: CompetitorNewsItem[] }>(
    `/api/competitor-news${q ? `?${q}` : ''}`,
  );
  return r.items;
}

export async function fetchNewsBrands(): Promise<NewsBrandsResponse> {
  return api.get<NewsBrandsResponse>('/api/competitor-news/brands');
}
