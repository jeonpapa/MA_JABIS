import { api } from './client';

export interface HomeBrand {
  id: number;
  brand: string;
  therapeutic_area: string | null;
  source: 'seed' | 'related';
  related_from: string | null;
  active: number; // SQLite INTEGER 0/1 — not a real boolean
  related_terms: string[]; // 승인된 보조 검색어 (seed 행 전용) — 시드 검색을 넓히는 하위 질의
  created_at: string;
  updated_at: string;
}

export interface HomeBrandInput {
  brand: string;
  therapeutic_area?: string | null;
  source?: 'seed' | 'related';
  related_from?: string | null;
  active?: boolean;
}

export interface HomeBrandPatch {
  brand?: string;
  therapeutic_area?: string | null;
  source?: 'seed' | 'related';
  related_from?: string | null;
  active?: boolean;
  related_terms?: string[];
}

export interface HomeBrandApproveResult {
  ok: true;
  seed: string;
  term: string;
  related_terms: string[];
}

export interface HomeBrandExpandResult {
  seeds_processed: number;
  candidates_added: number;
}

export async function listHomeBrands(opts?: {
  source?: 'seed' | 'related';
  active?: 0 | 1;
}): Promise<HomeBrand[]> {
  const params = new URLSearchParams();
  if (opts?.source) params.set('source', opts.source);
  if (opts?.active !== undefined) params.set('active', String(opts.active));
  const qs = params.toString();
  const r = await api.get<{ items: HomeBrand[] }>(
    `/api/admin/home-brands${qs ? `?${qs}` : ''}`,
  );
  return r.items;
}

export async function createHomeBrand(input: HomeBrandInput): Promise<HomeBrand> {
  const r = await api.post<{ item: HomeBrand }>('/api/admin/home-brands', input);
  return r.item;
}

export async function updateHomeBrand(
  id: number,
  patch: HomeBrandPatch,
): Promise<HomeBrand> {
  const r = await api.patch<{ item: HomeBrand }>(
    `/api/admin/home-brands/${id}`,
    patch,
  );
  return r.item;
}

export async function deleteHomeBrand(id: number): Promise<void> {
  await api.delete<{ ok: true }>(`/api/admin/home-brands/${id}`);
}

export async function expandHomeBrands(): Promise<HomeBrandExpandResult> {
  return api.post<HomeBrandExpandResult>('/api/admin/home-brands/expand');
}

/** related 후보 승인 — 원본 시드의 보조 검색어(related_terms)로 편입 + 후보 행 삭제 */
export async function approveHomeBrandCandidate(
  id: number,
): Promise<HomeBrandApproveResult> {
  return api.post<HomeBrandApproveResult>(`/api/admin/home-brands/${id}/approve`);
}
