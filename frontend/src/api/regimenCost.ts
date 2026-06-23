import { api } from './client';

// 투약비용비교 — 국내약가 기반 레지멘(약제 2~5개) 구성 → 일/월/연 치료비 비교.
// 비용은 enrichBulk 스냅샷(저장 시점). 서버 DB(regimen_comparisons)에 payload 통째 저장.

export type PriceSource = 'domestic' | 'weighted_avg';

export interface DoseInfo {
  schedule?: string | null;            // continuous|cycle|as_needed
  dailyDoseMg?: number | null;
  dailyDoseUnits?: number | null;
  cycleDays?: number | null;
  dosesPerCycle?: number | null;
  perKgMg?: number | null;
  perM2Mg?: number | null;
  indication?: string | null;
  alternatives?: { indication?: string; schedule?: string; daily_dose_mg?: number | null;
                   cycle_days?: number | null; doses_per_cycle?: number | null }[];
  confidence?: string | null;          // high|medium|low
  source?: string | null;              // regex|llm|review|manual|enrichment(보조)|none
  basis?: string | null;               // 계산식 설명
}

export interface DoseOverride {
  schedule?: string;
  dailyDoseMg?: number | null;
  dailyDoseUnits?: number | null;
  cycleDays?: number | null;
  dosesPerCycle?: number | null;
}

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
  doseInfo?: DoseInfo;            // 허가사항 기반 해석 용법(스냅샷)
  doseOverride?: DoseOverride;    // 사용자 수동 보정(있으면 우선)
}

// ── 항암 레지멘(정본 DB) ──
export interface Patient {
  height: number; weight: number; age: number; sex: 'M' | 'F'; scr: number;
}
export const PATIENT_DEFAULT: Patient = { height: 165, weight: 62, age: 60, sex: 'M', scr: 0.9 };

export interface OncoDrug {
  ingredient: string;          // 영문 INN (표시·dosing)
  dose_value: number | null;
  unit: string | null;         // mg/m2 | mg/kg | AUC | mg | 정 …
  dose_days?: string | null;
  per_cycle: number | null;    // 회수/주기
  cycle_days: number | null;
  cycle_label?: string | null; // q3w
  total_cycles: number | null;
  route?: string | null;
  note?: string | null;
  verify?: string | null;
  // 통합 행 모델 — dosing 출처 + 가격 소스(행별)
  dose_source?: string;        // saved|onco_db|mfds_label|manual|none
  price_source?: PriceSource;  // 행별 가격 소스
  price_ref?: string;          // WAP main_ingredient_code / 브랜드 insurance_code
  price_inn?: string;          // 가격조회용 INN (없으면 ingredient)
  // onco/cost 계산 결과
  one_dose_mg?: number | null;
  cycle_total_mg?: number | null;
  dose_basis?: string;
  price?: { available: boolean; reason?: string; label?: string; unit_price?: number;
            content_mg?: number; price_per_mg?: number; period?: string; source?: string };
  cost?: { cycle: number | null; course: number | null; monthly: number | null;
           yearly: number | null; daily: number | null };
}

export interface Regimen {
  name: string;
  kind?: 'manual' | 'onco';     // 기본 manual (레거시)
  drugs: RegimenDrug[];         // manual 약제(2~5)
  oncoRef?: number;             // onco 레지멘 ref(참고)
  oncoDrugs?: OncoDrug[];       // onco 약제 테이블
  patient?: Patient;            // onco 환자 파라미터
  metrics?: { bsa: number; gfr: number; crcl: number };  // 산출 스냅샷
  oncoTotals?: { cycle: number; course: number; monthly: number; yearly: number;
                 daily: number; hasMissing: boolean };
}

export interface RegimenPayload {
  base: Regimen;
  comparators: Regimen[]; // 최대 5개
  asOfDate?: string;      // 비교 기준일 'YYYY-MM-DD'
  source?: PriceSource;   // 비교 공통 가격 소스
  patient?: Patient;      // 비교 공통 환자 파라미터(onco)
  snapshotDate?: string;  // (구) 저장 시점 — asOfDate 없을 때 fallback
}

// onco DB 검색/조회/비용
export interface OncoRegimenHit {
  ref: number; regimen_id: string; cancer: string; regimen_name: string;
  therapy?: string; line?: string; drug_count: number; drug_names: string[];
}
export interface OncoCostResponse {
  metrics: { bsa: number; gfr: number; crcl: number; weight: number };
  source: PriceSource; asOfDate: string;
  drugs: OncoDrug[];
  totals: { cycle: number; course: number; monthly: number; yearly: number; daily: number; hasMissing: boolean };
}

export async function oncoSearch(q: string): Promise<OncoRegimenHit[]> {
  const res = await api.get<{ results: OncoRegimenHit[] }>(`/api/regimen/onco/search?q=${encodeURIComponent(q)}`);
  return res.results || [];
}
export async function oncoGet(ref: number): Promise<{ ref: number; regimen_name: string; cancer: string; drugs: OncoDrug[] }> {
  return api.get(`/api/regimen/onco/${ref}`);
}
export async function oncoCost(date: string, source: PriceSource, patient: Patient, drugs: OncoDrug[]): Promise<OncoCostResponse> {
  return api.post<OncoCostResponse>('/api/regimen/onco/cost', { date, source, patient, drugs });
}

/** 단일 약제 추가 시 기본 dosing 행 (저장→onco DB→허가사항→빈). */
export async function drugDosing(inn: string, ingredient?: string): Promise<OncoDrug & { dose_source: string }> {
  const q = `inn=${encodeURIComponent(inn)}${ingredient ? `&ingredient=${encodeURIComponent(ingredient)}` : ''}`;
  return api.get<OncoDrug & { dose_source: string }>(`/api/regimen/drug-dosing?${q}`);
}

/** 사용자 수정 dosing 영구 저장. */
export async function saveDrugDosing(inn: string, row: Partial<OncoDrug>): Promise<void> {
  await api.post('/api/regimen/drug-dosing', { inn, ...row });
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
  name?: string;
  doseOverride?: DoseOverride;
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
  doseInfo?: DoseInfo;
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
