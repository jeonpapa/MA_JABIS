import { api } from './client';

// 투약비용비교 — 국내약가 기반 레지멘(약제 2~5개) 구성 → 일/월/연 치료비 비교.
// 비용은 enrichBulk 스냅샷(저장 시점). 서버 DB(regimen_comparisons)에 payload 통째 저장.

export type PriceSource = 'domestic' | 'weighted_avg';

export interface RegimenDrug {
  insuranceCode: string;          // domestic: 보험코드. WAP: '' (mainIngredientCode 사용)
  name: string;
  ingredient: string;
  currentPrice: number | null;    // 해당 기준일 as-of 가격(표시가)
  dailyCost: number | null;
  monthlyCost: number | null;
  yearlyCost: number | null;
  // ── as-of/소스 (append-only — 기존 저장본 로드 호환) ──
  source?: PriceSource;
  normalizedName?: string;        // domestic 재가격용 enrichment 키
  mainIngredientCode?: string;    // weighted_avg 재가격용 주성분코드
  priceDate?: string;             // 실제 적용일(domestic=apply_date, WAP="YYYY 반기")
  available?: boolean;            // false → 해당 시점 가격 없음
}

export interface Regimen {
  name: string;
  drugs: RegimenDrug[];   // 2~5개
}

export interface RegimenPayload {
  base: Regimen;
  comparators: Regimen[]; // 최대 5개
  asOfDate?: string;      // 비교 기준일 'YYYY-MM-DD'
  snapshotDate?: string;  // (구) 저장 시점 — asOfDate 없을 때 fallback
}

// ── 주성분 가중평균(WAP) 외부 API ──
export interface WapResult {
  main_ingredient_code: string;
  ingredient_name: string;
  weighted_avg_price: number | null;
  match_mode?: string;
  period?: string;
}
export interface WapResponse {
  available: boolean;
  reason?: string;
  period?: string;
  fallback_previous?: boolean;
  results?: WapResult[];
}

export async function wapSearch(q: string, date: string): Promise<WapResponse> {
  return api.get<WapResponse>(
    `/api/regimen/wap?date=${encodeURIComponent(date)}&q=${encodeURIComponent(q)}`);
}

// ── 기준일 as-of 가격·치료비 배치 산출 ──
export interface PriceAsOfItem {
  source: PriceSource;
  insuranceCode?: string;
  normalizedName?: string;
  productName?: string;
  ingredient?: string;
  codes?: string[];
  mainIngredientCode?: string;
  ingredientName?: string;
}
export interface PriceAsOfResult {
  source: PriceSource;
  available: boolean;
  reason?: string;
  price: number | null;
  priceDate?: string;
  dailyCost: number | null;
  monthlyCost: number | null;
  yearlyCost: number | null;
  name?: string;
  ingredient?: string;
  insuranceCode?: string;
  mainIngredientCode?: string;
  isRsa?: boolean | null;
  fallbackPrevious?: boolean;
}

export async function priceAsOf(date: string, items: PriceAsOfItem[]): Promise<PriceAsOfResult[]> {
  const res = await api.post<{ results: PriceAsOfResult[] }>(
    '/api/regimen/price-as-of', { date, items });
  return res.results || [];
}

export interface RegimenComparison {
  id: number;
  name: string;
  owner_email?: string;
  payload: RegimenPayload;
  created_at?: string;
  updated_at?: string;
}

export async function listRegimens(): Promise<RegimenComparison[]> {
  const res = await api.get<{ items: RegimenComparison[] }>('/api/regimen-comparisons');
  return res.items || [];
}

export async function createRegimen(name: string, payload: RegimenPayload): Promise<RegimenComparison> {
  return api.post<RegimenComparison>('/api/regimen-comparisons', { name, payload });
}

export async function updateRegimen(id: number, name: string, payload: RegimenPayload): Promise<RegimenComparison> {
  return api.put<RegimenComparison>(`/api/regimen-comparisons/${id}`, { name, payload });
}

export async function deleteRegimen(id: number): Promise<void> {
  await api.delete(`/api/regimen-comparisons/${id}`);
}

/** 레지멘 총 일/월/연 치료비 = 구성 약제 합산 (null 은 0 취급, 일부 미상 표시). */
export function regimenTotals(r: Regimen): { daily: number; monthly: number; yearly: number; hasMissing: boolean } {
  let daily = 0, monthly = 0, yearly = 0, hasMissing = false;
  for (const d of r.drugs) {
    if (d.dailyCost == null && d.monthlyCost == null && d.yearlyCost == null) hasMissing = true;
    daily += d.dailyCost ?? 0;
    monthly += d.monthlyCost ?? (d.dailyCost != null ? d.dailyCost * 30 : 0);
    yearly += d.yearlyCost ?? (d.dailyCost != null ? d.dailyCost * 365 : 0);
  }
  return { daily, monthly, yearly, hasMissing };
}
