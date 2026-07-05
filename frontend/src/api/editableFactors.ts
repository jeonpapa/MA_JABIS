import { api } from './client';

// 관리자 편집 가능 팩터 — 추적 경쟁 브랜드/MNC + 뉴스 키워드 팩터 (DB 이관, Phase 2)
// 백엔드: /api/admin/competitor-brands, /api/admin/news-factors (admin-only)

export interface CompetitorBrand {
  id: number;
  query: string;
  company: string;
  anchor: string | null;
  kind: 'competitor' | 'msd_asset';
  logo: string | null;
  color: string | null;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface CompetitorBrandInput {
  query: string;
  company: string;
  anchor?: string | null;
  kind?: 'competitor' | 'msd_asset';
  logo?: string | null;
  color?: string | null;
  active?: boolean;
}

export async function listCompetitorBrands(): Promise<CompetitorBrand[]> {
  const r = await api.get<{ items: CompetitorBrand[] }>('/api/admin/competitor-brands');
  return r.items;
}

export async function createCompetitorBrand(input: CompetitorBrandInput): Promise<CompetitorBrand> {
  const r = await api.post<{ item: CompetitorBrand }>('/api/admin/competitor-brands', input);
  return r.item;
}

export async function updateCompetitorBrand(
  id: number,
  patch: Partial<CompetitorBrandInput>,
): Promise<CompetitorBrand> {
  const r = await api.patch<{ item: CompetitorBrand }>(`/api/admin/competitor-brands/${id}`, patch);
  return r.item;
}

export async function deleteCompetitorBrand(id: number): Promise<void> {
  await api.delete<{ ok: true }>(`/api/admin/competitor-brands/${id}`);
}

export type NewsFactorScope = 'competitor' | 'gov';
export type NewsFactorKind = 'relevance' | 'context_anchor' | 'gov_seed';

export interface NewsFactor {
  id: number;
  scope: NewsFactorScope;
  kind: NewsFactorKind;
  agency: string | null;
  term: string;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface NewsFactorInput {
  scope: NewsFactorScope;
  kind: NewsFactorKind;
  agency?: string | null;
  term: string;
  active?: boolean;
}

export async function listNewsFactors(scope?: NewsFactorScope, kind?: NewsFactorKind): Promise<NewsFactor[]> {
  const qs = new URLSearchParams();
  if (scope) qs.set('scope', scope);
  if (kind) qs.set('kind', kind);
  const q = qs.toString();
  const r = await api.get<{ items: NewsFactor[] }>(`/api/admin/news-factors${q ? `?${q}` : ''}`);
  return r.items;
}

export async function createNewsFactor(input: NewsFactorInput): Promise<NewsFactor> {
  const r = await api.post<{ item: NewsFactor }>('/api/admin/news-factors', input);
  return r.item;
}

export async function updateNewsFactor(id: number, patch: Partial<NewsFactorInput>): Promise<NewsFactor> {
  const r = await api.patch<{ item: NewsFactor }>(`/api/admin/news-factors/${id}`, patch);
  return r.item;
}

export async function deleteNewsFactor(id: number): Promise<void> {
  await api.delete<{ ok: true }>(`/api/admin/news-factors/${id}`);
}
