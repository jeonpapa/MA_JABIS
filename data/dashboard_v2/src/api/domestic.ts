import { api, getToken } from './client';

export interface DomesticPriceHistoryEntry {
  date: string;
  price: number;
  type: string;
  reason: string;
  changeRate: number | null;
}

export interface DomesticAnalogue {
  name: string;
  ingredient: string;
  price: number;
  dailyCost: number | null;
  company: string;
  approvalDate: string | null;
  coverageStart: string | null;
  usageText: string | null;
  dosageForm: string | null;
}

export interface DomesticProduct {
  id: string;
  productName: string;
  brandName: string;
  ingredient: string;
  insuranceCode: string;
  mergedCodes: string[];
  company: string;
  mergedCompanies: string[];
  normalizedName: string;
  dosageForm: string;
  firstRegistDate: string;
  firstPrice: number;
  currentPrice: number;
  priceChangeCount: number;
  changeRateFromFirst: number;
  change: number | null;
  lastUpdated: string;
  status: string;
  statusDetail: string;
  priceHistory: DomesticPriceHistoryEntry[];
  sameIngredientCount: number;
  analogues: DomesticAnalogue[];

  // enrichment (drug_enrichment LEFT JOIN) — 없으면 null
  firstApprovalDate: string | null;   // 식약처 최초 허가일
  coverageStart: string | null;       // 급여 등재일 (merged_codes 의 earliest)
  dosage: string | null;              // 용법용량
  dailyCost: number | null;
  monthlyCost: number | null;
  yearlyCost: number | null;
  enrichmentConfidence: string | null;

  // 기타 — 현재 서버 미제공
  category: string | null;
  hasRSA: boolean | null;
  rsaType: string | null;
  evalCommitteeDoc: string | null;
}

interface RawHistoryRow {
  date: string;
  price: number;
  delta_pct: number | null;
  base_price_change_rate: number;
  change_type: string;
  price_change: number;
  is_first: boolean;
}

interface RawProduct {
  insurance_code: string;
  product_name: string;
  brand_name: string;
  ingredient: string;
  dosage_form: string;
  company: string;
  first_date: string;
  current_price: number;
  merged_codes?: string[];
  merged_companies?: string[];
  normalized_name?: string;
  status?: string;
  status_detail?: string;
  price_history: RawHistoryRow[];
  approval_date?: string | null;
  usage_text?: string | null;
  coverage_start?: string | null;
  daily_cost?: number | null;
  monthly_cost?: number | null;
  yearly_cost?: number | null;
  enrichment_confidence?: string | null;
}

interface RawResponse {
  query: string;
  products: RawProduct[];
  dosage_forms: string[];
}

function toIsoDate(dot: string): string {
  // "2024.07.01" → "2024-07-01"
  return dot.replace(/\./g, '-');
}

function typeLabel(changeType: string, isFirst: boolean): string {
  if (isFirst || changeType === '최초') return '최초등재';
  if (changeType === '인상') return '약가인상';
  if (changeType === '인하') return '약가인하';
  return '유지';
}

function reasonLabel(changeType: string, isFirst: boolean): string {
  if (isFirst || changeType === '최초') return '신규 등재';
  return '약가 재평가';
}

function mapHistory(rows: RawHistoryRow[]): DomesticPriceHistoryEntry[] {
  return rows.map(r => ({
    date: toIsoDate(r.date),
    price: r.price,
    type: typeLabel(r.change_type, r.is_first),
    reason: reasonLabel(r.change_type, r.is_first),
    changeRate: r.delta_pct,
  }));
}

function mapProduct(raw: RawProduct, allRaw: RawProduct[]): DomesticProduct {
  const history = mapHistory(raw.price_history);
  const firstPrice = history[0]?.price ?? raw.current_price;
  const lastPrice = history[history.length - 1]?.price ?? raw.current_price;
  const baseRate = raw.price_history[raw.price_history.length - 1]?.base_price_change_rate ?? 0;
  const lastDelta = raw.price_history[raw.price_history.length - 1]?.delta_pct ?? null;
  const sameIng = allRaw.filter(p => p.ingredient && p.ingredient === raw.ingredient).length;
  // 비교 약제 = 동일 성분 + 다른 브랜드/코드. 성분 없으면 검색결과 내 다른 제품을 보여줌.
  const analogues: DomesticAnalogue[] = allRaw
    .filter(p => {
      if (p.insurance_code === raw.insurance_code) return false;
      if (p.normalized_name && raw.normalized_name && p.normalized_name === raw.normalized_name) return false;
      if (raw.ingredient && p.ingredient) return p.ingredient === raw.ingredient;
      return true;  // 성분 미상 시 검색결과 내 타제품 전부 후보
    })
    .slice(0, 100)
    .map(p => ({
      name: p.brand_name || p.product_name,
      ingredient: p.ingredient,
      price: p.current_price,
      dailyCost: p.daily_cost ?? null,
      company: p.company,
      approvalDate: p.approval_date ?? null,
      coverageStart: p.coverage_start ?? null,
      usageText: p.usage_text ?? null,
      dosageForm: p.dosage_form ?? null,
    }));

  return {
    id: raw.insurance_code,
    productName: raw.brand_name || raw.product_name,
    brandName: raw.brand_name,
    ingredient: raw.ingredient,
    insuranceCode: raw.insurance_code,
    mergedCodes: raw.merged_codes ?? [raw.insurance_code],
    company: raw.company,
    mergedCompanies: raw.merged_companies ?? (raw.company ? [raw.company] : []),
    normalizedName: raw.normalized_name ?? raw.brand_name,
    dosageForm: raw.dosage_form,
    firstRegistDate: toIsoDate(raw.first_date),
    firstPrice,
    currentPrice: lastPrice,
    priceChangeCount: Math.max(0, history.length - 1),
    changeRateFromFirst: Math.round(baseRate * 100) / 100,
    change: lastDelta,
    lastUpdated: history[history.length - 1]?.date ?? toIsoDate(raw.first_date),
    status: raw.status ?? 'active',
    statusDetail: raw.status_detail ?? '',
    priceHistory: history,
    sameIngredientCount: sameIng || 1,
    analogues,

    firstApprovalDate: raw.approval_date ?? null,
    coverageStart: raw.coverage_start ?? null,
    dosage: raw.usage_text ?? null,
    dailyCost: raw.daily_cost ?? null,
    monthlyCost: raw.monthly_cost ?? null,
    yearlyCost: raw.yearly_cost ?? null,
    enrichmentConfidence: raw.enrichment_confidence ?? null,

    category: null,
    hasRSA: null,
    rsaType: null,
    evalCommitteeDoc: null,
  };
}

export async function searchDomesticPriceChanges(query: string): Promise<DomesticProduct[]> {
  const q = query.trim();
  if (!q) return [];
  const res = await api.get<RawResponse>(
    `/api/domestic/price-changes?q=${encodeURIComponent(q)}`,
  );
  return res.products.map(p => mapProduct(p, res.products));
}

// 자유 검색으로 비교약제 풀 확장 — 성분이 달라도 가능
export async function searchAnalogues(
  query: string,
  excludeInsuranceCode?: string,
): Promise<DomesticAnalogue[]> {
  const q = query.trim();
  if (q.length < 2) return [];
  const res = await api.get<RawResponse>(
    `/api/domestic/price-changes?q=${encodeURIComponent(q)}`,
  );
  const seen = new Set<string>();
  const out: DomesticAnalogue[] = [];
  for (const p of res.products) {
    if (excludeInsuranceCode && p.insurance_code === excludeInsuranceCode) continue;
    const name = p.brand_name || p.product_name;
    if (seen.has(name)) continue;
    seen.add(name);
    out.push({
      name,
      ingredient: p.ingredient,
      price: p.current_price,
      dailyCost: p.daily_cost ?? null,
      company: p.company,
      approvalDate: p.approval_date ?? null,
      coverageStart: p.coverage_start ?? null,
      usageText: p.usage_text ?? null,
      dosageForm: p.dosage_form ?? null,
    });
  }
  return out;
}

export function exportDomesticPriceChangesUrl(query: string, format: 'xlsx' | 'csv' = 'xlsx'): string {
  return `/api/domestic/price-changes/export?q=${encodeURIComponent(query)}&format=${format}`;
}

export interface ChangeReasonResult {
  mechanism: string;
  mechanism_label: string;
  reason: string;
  confidence: string;
  evidence_summary?: string;
  references?: { title: string; url: string; media: string; published_at?: string }[];
  cached?: boolean;
  review?: { approved?: boolean };
}

export async function fetchChangeReason(params: {
  drug: string;
  date: string;
  ingredient?: string;
  deltaPct?: number | null;
  refresh?: boolean;
}): Promise<ChangeReasonResult> {
  const q = new URLSearchParams({
    drug: params.drug,
    date: params.date.replace(/-/g, '.'),
  });
  if (params.ingredient) q.set('ingredient', params.ingredient);
  if (params.deltaPct != null) q.set('delta_pct', String(params.deltaPct));
  if (params.refresh) q.set('refresh', '1');
  return api.get<ChangeReasonResult>(`/api/domestic/change-reason?${q.toString()}`);
}

export async function downloadDomesticExport(query: string, format: 'xlsx' | 'csv' = 'xlsx'): Promise<void> {
  const token = getToken();
  const res = await fetch(exportDomesticPriceChangesUrl(query, format), {
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  });
  if (!res.ok) throw new Error(`export failed: HTTP ${res.status}`);
  const blob = await res.blob();
  const cd = res.headers.get('Content-Disposition') || '';
  const m = cd.match(/filename\*?=(?:UTF-8'')?([^;]+)/i);
  const filename = m ? decodeURIComponent(m[1].replace(/^"|"$/g, '')) : `domestic_${query}.${format}`;
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
