import { api } from './client';

export interface BrandNewsItem {
  title: string;
  url: string;
  source: string;
  date: string;
  description: string;
}

export interface BrandTrafficEntry {
  brand: string;
  total_count: number;
  daily: Record<string, number>;
  sparkline: number[];
  latest_news: BrandNewsItem[];
}

export interface MediaIntelligenceResponse {
  updated_at: string;
  days: number;
  brands: BrandTrafficEntry[];
  error?: string;
}

export async function fetchMediaIntelligence(days?: number, refresh = false): Promise<MediaIntelligenceResponse> {
  const q = new URLSearchParams();
  if (typeof days === 'number') q.set('days', String(days));
  if (refresh) q.set('refresh', '1');
  const qs = q.toString();
  return api.get<MediaIntelligenceResponse>(`/api/home/media-intelligence${qs ? `?${qs}` : ''}`);
}

export async function fetchBrandNews(brand: string, limit = 10): Promise<{ brand: string; count: number; items: BrandNewsItem[] }> {
  const q = new URLSearchParams({ brand, limit: String(limit) });
  return api.get(`/api/home/brand-news?${q.toString()}`);
}
