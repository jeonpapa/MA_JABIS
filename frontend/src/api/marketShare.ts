import { api, getToken } from './client';

// ─────────────────────────────────────────────────────────────────────────────
// Backend response types — api/server.py /api/market-share/* (IQVIA NSA-E)
// ─────────────────────────────────────────────────────────────────────────────

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

// ─────────────────────────────────────────────────────────────────────────────
// Fetchers
// ─────────────────────────────────────────────────────────────────────────────

export function searchMarketShare(q: string, limit = 30): Promise<MsSearchResult> {
  const params = new URLSearchParams({ q: normalizeSearchQuery(q), limit: String(limit) });
  return api.get<MsSearchResult>(`/api/market-share/search?${params.toString()}`);
}

export function fetchAtc4(code: string, quarter?: string): Promise<MsAtc4Response> {
  const q = quarter ? `?quarter=${encodeURIComponent(quarter)}` : '';
  return api.get<MsAtc4Response>(`/api/market-share/atc4/${encodeURIComponent(code)}${q}`);
}

export function fetchAtc4Trend(code: string, top = 5): Promise<MsTrendResponse> {
  return api.get<MsTrendResponse>(`/api/market-share/atc4/${encodeURIComponent(code)}/trend?top=${top}`);
}

export function fetchBrand(name: string, atc4: string): Promise<MsBrandResponse> {
  const params = new URLSearchParams({ name, atc4 });
  return api.get<MsBrandResponse>(`/api/market-share/brand?${params.toString()}`);
}

/** 백엔드 xlsx export — Market Share + Unit Trend + Revenue Trend 3개 시트. */
export async function downloadMarketShareXlsx(atc4: string, quarter: string, top = 8): Promise<void> {
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

// ─────────────────────────────────────────────────────────────────────────────
// Default market resolution — 키트루다(KEYTRUDA)의 ATC4 시장(PD-1/PD-L1)
// ─────────────────────────────────────────────────────────────────────────────

/** IQVIA DB 의 키트루다 시장 (MAB A-NEOPLAS PD-1/PD-L1). 검색 실패 시 fallback. */
export const DEFAULT_ATC4 = 'L01G5';
const DEFAULT_MARKET_QUERY = 'keytruda';

/** DB 의 product_name/molecule_desc 는 영문 — 주요 국문 표기를 영문 질의로 변환. */
const KR_QUERY_ALIASES: Record<string, string> = {
  '키트루다': 'keytruda',
  '펨브롤리주맙': 'pembrolizumab',
  '옵디보': 'opdivo',
  '니볼루맙': 'nivolumab',
  '티쎈트릭': 'tecentriq',
  '아테졸리주맙': 'atezolizumab',
  '임핀지': 'imfinzi',
  '더발루맙': 'durvalumab',
  '바벤시오': 'bavencio',
  '아벨루맙': 'avelumab',
  '젬퍼리': 'jemperli',
  '도스탈리맙': 'dostarlimab',
};

export function normalizeSearchQuery(q: string): string {
  const t = q.trim();
  return KR_QUERY_ALIASES[t] ?? t;
}

/** 기본 시장 ATC4 결정 — 키트루다 검색으로 동적 해석, 실패 시 L01G5. */
export async function resolveDefaultAtc4(): Promise<string> {
  try {
    const r = await searchMarketShare(DEFAULT_MARKET_QUERY, 1);
    return r.items[0]?.atc4_code || DEFAULT_ATC4;
  } catch {
    return DEFAULT_ATC4;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Formatting helpers
// ─────────────────────────────────────────────────────────────────────────────

/** "2025Q4" → "Q4 25" */
export function quarterLabel(q: string): string {
  const m = /^(\d{4})Q(\d)$/.exec(q);
  if (!m) return q;
  return `Q${m[2]} ${m[1].slice(2)}`;
}

/** values_lc 는 원 단위 — 백만원으로 표기. */
export function formatLcKrw(v: number): string {
  const m = v / 1_000_000;
  return m.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

// ─────────────────────────────────────────────────────────────────────────────
// Admin — 분기 xlsx 업로드 (POST /api/admin/market-share/upload, GET …/uploads)
// ─────────────────────────────────────────────────────────────────────────────

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
  // FormData 는 fetch 가 multipart boundary 를 자동 설정 — api.post 는 JSON 전용이므로 직접 fetch
  const token = getToken();
  const res = await fetch('/api/admin/market-share/upload', {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: fd,
  });
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try { data = JSON.parse(text); } catch { data = text; }
  }
  if (!res.ok) {
    const message =
      (data && typeof data === 'object' && 'error' in data && typeof (data as { error?: unknown }).error === 'string')
        ? (data as { error: string }).error
        : `HTTP ${res.status}`;
    throw new Error(message);
  }
  return data as MsIngestResult;
}

// ─────────────────────────────────────────────────────────────────────────────
// Readdy-shape adapters
// ─────────────────────────────────────────────────────────────────────────────

/** readdy mock 팔레트 — 도넛/요약 카드 (mocks/dashboardData.marketShareData). */
export const PIE_COLORS = ['#00E5CC', '#7C3AED', '#F59E0B', '#EF4444', '#10B981'];
export const OTHERS_COLOR = '#6B7280';
/** readdy 트렌드 라인 팔레트 (page CHART_COLORS). */
export const LINE_COLORS = ['#0D9488', '#7C3AED', '#D97706', '#EF4444', '#10B981'];

export interface PieSlice {
  name: string;
  value: number;   // values_share_pct (%)
  units: number;   // dosage_units
  color: string;
  mfrName?: string;
  isOthers?: boolean;
}

/**
 * ATC4 시장 → readdy marketShareData 형태.
 * 상위 topN 브랜드 + 잔여 합산 '기타' (readdy mock 과 동일: 5 + 기타 = 6 슬라이스).
 * 매출 0 인 브랜드는 제외 (해당 분기 데이터 없음 — 임의 0% 표시 금지).
 */
export function buildPieData(market: MsAtc4Response, topN = 5): PieSlice[] {
  const products = market.products.filter(p => (p.values_lc ?? 0) > 0 || (p.dosage_units ?? 0) > 0);
  const top = products.slice(0, topN);
  const rest = products.slice(topN);
  const items: PieSlice[] = top.map((p, i) => ({
    name: p.product_name,
    value: Number(p.values_share_pct.toFixed(1)),
    units: Math.round(p.dosage_units),
    color: PIE_COLORS[i % PIE_COLORS.length],
    mfrName: p.mfr_name,
  }));
  if (rest.length > 0) {
    const pct = rest.reduce((a, p) => a + p.values_share_pct, 0);
    const units = rest.reduce((a, p) => a + p.dosage_units, 0);
    items.push({
      name: `기타 (${rest.length})`,
      value: Number(pct.toFixed(1)),
      units: Math.round(units),
      color: OTHERS_COLOR,
      isOthers: true,
    });
  }
  return items;
}

export type TrendKind = 'share' | 'units' | 'revenue';

export interface TrendRow {
  quarter: string;
  [brand: string]: string | number;
}

/**
 * 트렌드 시계열 → readdy 차트 행 형태 [{quarter, <Brand>: number}].
 * - share: values_share (%), 소수 1자리
 * - units: dosage_units (정수)
 * - revenue: values_lc 원 → 백만원 (정수)
 * 해당 분기 데이터가 없는 브랜드는 키 자체를 생략 (값 조작 금지 — 라인 단절로 표시).
 */
export function buildTrendRows(trend: MsTrendResponse, kind: TrendKind, lastN = 6): TrendRow[] {
  const quarters = trend.quarters.slice(-lastN);
  return quarters.map(q => {
    const row: TrendRow = { quarter: quarterLabel(q) };
    trend.top_brands.forEach(b => {
      const s = trend.series[b];
      if (!s) return;
      if (kind === 'share') {
        const v = s.values_share?.[q];
        if (v != null) row[b] = Number(v.toFixed(1));
      } else if (kind === 'units') {
        const v = s.units?.[q];
        if (v != null) row[b] = Math.round(v);
      } else {
        const v = s.values?.[q];
        if (v != null) row[b] = Math.round(v / 1_000_000); // 원 → 백만원
      }
    });
    return row;
  });
}
