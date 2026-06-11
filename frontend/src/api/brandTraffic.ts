import { api } from './client';

export interface BrandNews {
  title: string;
  source: string;
  date: string;
  tag: string;
  url: string;
}

export interface BrandTrafficItem {
  id: number;
  rank: number;
  brand: string;
  company: string | null;
  category: string | null;
  color: string | null;
  trafficIndex: number;
  change: number;
  sparkline: number[];
  news: BrandNews[];
  created_at: string;
  updated_at: string;
}

export interface BrandTrafficInput {
  rank?: number;
  brand: string;
  company?: string | null;
  category?: string | null;
  color?: string | null;
  trafficIndex?: number;
  change?: number;
  sparkline?: number[];
  news?: BrandNews[];
}

export async function listBrandTraffic(): Promise<BrandTrafficItem[]> {
  const r = await api.get<{ items: BrandTrafficItem[] }>('/api/brand-traffic');
  return r.items;
}

export async function createBrandTraffic(input: BrandTrafficInput): Promise<BrandTrafficItem> {
  const r = await api.post<{ item: BrandTrafficItem }>('/api/admin/brand-traffic', input);
  return r.item;
}

export async function updateBrandTraffic(
  id: number,
  patch: Partial<BrandTrafficInput>
): Promise<BrandTrafficItem> {
  const r = await api.patch<{ item: BrandTrafficItem }>(`/api/admin/brand-traffic/${id}`, patch);
  return r.item;
}

export async function deleteBrandTraffic(id: number): Promise<void> {
  await api.delete<{ ok: true }>(`/api/admin/brand-traffic/${id}`);
}
