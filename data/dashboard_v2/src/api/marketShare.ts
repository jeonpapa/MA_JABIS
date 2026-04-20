import { api, getToken } from './client';

export interface MsSearchHit {
  product_name: string;
  molecule_desc: string;
  mfr_name: string;
  atc4_code: string;
  atc4_desc: string;
  pack_count: number;
  values_lc: number;
  dosage_units: number;
}

export interface MsSearchResult {
  quarter: string | null;
  items: MsSearchHit[];
}

export interface MsAtc4Product {
  product_name: string;
  molecule_desc: string;
  mfr_name: string;
  pack_count: number;
  values_lc: number;
  dosage_units: number;
  values_share_pct: number;
  units_share_pct: number;
}

export interface MsAtc4Response {
  atc4_code: string;
  atc4_desc: string;
  quarter: string;
  quarters: string[];
  totals: { values_lc: number; dosage_units: number };
  products: MsAtc4Product[];
}

export interface MsTrendResponse {
  atc4_code: string;
  atc4_desc: string;
  quarters: string[];
  top_brands: string[];
  series: Record<string, {
    values: Record<string, number>;
    units: Record<string, number>;
    values_share: Record<string, number>;
    units_share: Record<string, number>;
  }>;
}

export interface MsBrandPack {
  product_id: string;
  pack: string;
  pack_desc: string;
  strength: string;
  pack_launch_date: string | null;
}

export interface MsBrandQuarterly {
  quarter: string;
  values_lc: number;
  dosage_units: number;
}

export interface MsBrandResponse {
  product_name: string;
  molecule_desc: string;
  mfr_name: string;
  corp: string;
  mnc13: string;
  atc4_code: string;
  atc4_desc: string;
  quarter: string;
  quarters: string[];
  market_rank: number | null;
  market_share_pct: number;
  market_total_values_lc: number;
  packs: MsBrandPack[];
  quarterly: MsBrandQuarterly[];
}

export function searchMarketShare(q: string, limit = 30): Promise<MsSearchResult> {
  const params = new URLSearchParams({ q, limit: String(limit) });
  return api.get<MsSearchResult>(`/api/market-share/search?${params.toString()}`);
}

export function fetchAtc4(code: string, quarter?: string): Promise<MsAtc4Response> {
  const q = quarter ? `?quarter=${encodeURIComponent(quarter)}` : '';
  return api.get<MsAtc4Response>(`/api/market-share/atc4/${encodeURIComponent(code)}${q}`);
}

export function fetchAtc4Trend(code: string, top = 6): Promise<MsTrendResponse> {
  return api.get<MsTrendResponse>(`/api/market-share/atc4/${encodeURIComponent(code)}/trend?top=${top}`);
}

export function fetchBrand(name: string, atc4: string): Promise<MsBrandResponse> {
  const params = new URLSearchParams({ name, atc4 });
  return api.get<MsBrandResponse>(`/api/market-share/brand?${params.toString()}`);
}

export async function downloadMarketShareXlsx(
  atc4: string,
  quarter: string,
  top = 8,
): Promise<void> {
  const params = new URLSearchParams({ atc4, quarter, top: String(top) });
  const token = getToken();
  const res = await fetch(`/api/market-share/export?${params.toString()}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  });
  if (!res.ok) throw new Error(`export failed: HTTP ${res.status}`);
  const blob = await res.blob();
  const cd = res.headers.get('Content-Disposition') || '';
  const m = cd.match(/filename\*?=(?:UTF-8'')?([^;]+)/i);
  const filename = m ? decodeURIComponent(m[1].replace(/^"|"$/g, '')) : `MarketShare_${atc4}_${quarter}.xlsx`;
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function quarterLabel(q: string): string {
  // "2025Q4" → "Q4 25"
  const m = /^(\d{4})Q(\d)$/.exec(q);
  if (!m) return q;
  return `Q${m[2]} ${m[1].slice(2)}`;
}

export function formatLcKrw(v: number): string {
  // Excel values are in raw KRW; present as 백만원
  const m = v / 1_000_000;
  return m.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

// ─── Admin ──────────────────────────────────────────────────────────────────

export interface MsUploadEntry {
  id: number;
  uploaded_at: string;
  uploaded_by: string | null;
  filename: string | null;
  rows_ingested: number;
  quarters: string[];
}

export interface MsUploadsResponse {
  uploads: MsUploadEntry[];
  totals: {
    products: number;
    quarterly_points: number;
    quarters_available: string[];
  };
}

export interface MsIngestResult {
  filename: string;
  rows_ingested: number;
  unique_products: number;
  quarterly_points: number;
  quarters: string[];
}

export function fetchMarketShareUploads(): Promise<MsUploadsResponse> {
  return api.get<MsUploadsResponse>('/api/admin/market-share/uploads');
}

export async function uploadMarketShareXlsx(
  file: File,
  sheet = 'NSA'
): Promise<MsIngestResult> {
  const fd = new FormData();
  fd.append('file', file);
  fd.append('sheet', sheet);
  const token = getToken();
  const res = await fetch('/api/admin/market-share/upload', {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: fd,
  });
  const text = await res.text();
  let data: any = null;
  if (text) {
    try { data = JSON.parse(text); } catch { data = text; }
  }
  if (!res.ok) {
    const message = (data && data.error) || `HTTP ${res.status}`;
    throw new Error(message);
  }
  return data as MsIngestResult;
}
