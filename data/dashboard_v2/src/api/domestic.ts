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
  /** 'direct' | 'inherited_generic:<donor>' | 'default_heuristic' | null */
  enrichmentSource?: string | null;
  normalizedName?: string;
  insuranceCode?: string;
  bsaCalc?: {
    method: 'weight' | 'bsa';
    rationale: string;
    intervalDays: number;
    perDoseMg: number;
  } | null;
  usageUnverified?: boolean;
  /** RSA (위험분담제) 정보 — 1=대상, 0=해당 없음, null=확인 필요. 표시가 ≠ 실제가 */
  isRsa?: 0 | 1 | null;
  rsaType?: 'refund' | 'expenditure_cap' | 'utilization' | 'conditional' | 'combined' | null;
  rsaNote?: string | null;
  rsaSource?: string | null;
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
  enrichmentSource: string | null;
  bsaCalc?: {
    method: 'weight' | 'bsa';
    rationale: string;
    intervalDays: number;
    perDoseMg: number;
  } | null;
  usageUnverified?: boolean;
  /** RSA (위험분담제) — 1=대상 (표시가 ≠ 실제가), 0=해당 없음, null=확인 필요 */
  isRsa?: 0 | 1 | null;
  rsaType?: 'refund' | 'expenditure_cap' | 'utilization' | 'conditional' | 'combined' | null;
  rsaNote?: string | null;
  rsaSource?: string | null;

  /** 특허 상태 — '만료' | '유효' | null. MFDS 공공데이터 API 실측 또는 가격 history 추정 */
  patentStatus?: '만료' | '유효' | null;
  /** 등록 상태 물질특허의 가장 늦은 만료일 (YYYY-MM-DD). MFDS 실측 시에만 채워짐 */
  patentExpiryDate?: string | null;
  /** LOE 직전 가격 시점 (YYYY.MM.DD). 가격 history 추정 시에만 채워짐 */
  patentLoeDateInferred?: string | null;
  /** 데이터 출처: 'mfds_api' (실측) | 'price_history' (추정) | null */
  patentSource?: 'mfds_api' | 'price_history' | null;
  /** 출처/근거 설명 문구 */
  patentSourceNote?: string | null;
  /** 물질특허 상세 — MFDS 실측 시 노출 */
  patentSubstancePatents?: Array<{
    patent_no: string | null;
    patent_status: string;
    patent_end_date: string | null;
    patent_gb_code: string;
    invn_name: string | null;
    patentee: string | null;
  }>;
  /** 후속 특허 (ADC/조성/용도/제법 등) — LOE 결정 무관, 참고용 */
  patentSecondaryPatents?: Array<{
    patent_no: string | null;
    patent_status: string;
    patent_end_date: string | null;
    patent_gb_code: string;
    invn_name: string | null;
    patentee: string | null;
    reclassified_reason?: string;
  }>;
  /** 식약처 의약품 제품 허가정보 — DrugPrdtPrmsnInfoService07 실측 */
  mfdsPermit?: {
    itemSeq: string | null;
    itemEngName: string | null;
    permitHolder: string | null;
    permitDate: string | null;
    cancelStatus: string | null;
    etcOtc: string | null;
    atcCode: string | null;
    mainIngr: string | null;
    mainIngrEng: string | null;
    materialName: string | null;
    chart: string | null;
    packUnit: string | null;
    validTerm: string | null;
    storageMethod: string | null;
    rareDrugYn: string | null;
    newdrugClass: string | null;
    reexamTarget: string | null;
    reexamDate: string | null;
    changeDate: string | null;
    permitKind: string | null;
    effectText: string | null;
    cautionText: string | null;
    udDocUrl: string | null;
    eeDocUrl: string | null;
    nbDocUrl: string | null;
  } | null;

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
  enrichment_source?: string | null;
  bsa_calc?: {
    daily_cost: number;
    method: 'weight' | 'bsa';
    rationale: string;
    interval_days: number;
    per_dose_mg: number;
  } | null;
  usage_unverified?: boolean;
  is_rsa?: 0 | 1 | null;
  rsa_type?: 'refund' | 'expenditure_cap' | 'utilization' | 'conditional' | 'combined' | null;
  rsa_note?: string | null;
  rsa_source?: string | null;
  patent_status?: '만료' | '유효' | null;
  patent_expiry_date?: string | null;
  patent_loe_date_inferred?: string | null;
  patent_source?: 'mfds_api' | 'price_history' | null;
  patent_source_note?: string | null;
  patent_substance_patents?: Array<{
    patent_no: string | null;
    patent_status: string;
    patent_end_date: string | null;
    patent_gb_code: string;
    invn_name: string | null;
    patentee: string | null;
  }>;
  patent_secondary_patents?: Array<{
    patent_no: string | null;
    patent_status: string;
    patent_end_date: string | null;
    patent_gb_code: string;
    invn_name: string | null;
    patentee: string | null;
    reclassified_reason?: string;
  }>;
  mfds_permit?: {
    item_seq: string | null;
    item_eng_name: string | null;
    permit_holder: string | null;
    permit_date: string | null;
    cancel_status: string | null;
    etc_otc: string | null;
    atc_code: string | null;
    main_ingr: string | null;
    main_ingr_eng: string | null;
    material_name: string | null;
    chart: string | null;
    pack_unit: string | null;
    valid_term: string | null;
    storage_method: string | null;
    rare_drug_yn: string | null;
    newdrug_class: string | null;
    reexam_target: string | null;
    reexam_date: string | null;
    change_date: string | null;
    permit_kind: string | null;
    effect_text: string | null;
    caution_text: string | null;
    ud_doc_url: string | null;
    ee_doc_url: string | null;
    nb_doc_url: string | null;
    source: string | null;
  } | null;
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
      enrichmentSource: p.enrichment_source ?? null,
      normalizedName: p.normalized_name,
      insuranceCode: p.insurance_code,
      bsaCalc: p.bsa_calc ? {
        method: p.bsa_calc.method,
        rationale: p.bsa_calc.rationale,
        intervalDays: p.bsa_calc.interval_days,
        perDoseMg: p.bsa_calc.per_dose_mg,
      } : null,
      usageUnverified: p.usage_unverified ?? false,
      isRsa: p.is_rsa ?? null,
      rsaType: p.rsa_type ?? null,
      rsaNote: p.rsa_note ?? null,
      rsaSource: p.rsa_source ?? null,
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
    enrichmentSource: raw.enrichment_source ?? null,
    bsaCalc: raw.bsa_calc ? {
      method: raw.bsa_calc.method,
      rationale: raw.bsa_calc.rationale,
      intervalDays: raw.bsa_calc.interval_days,
      perDoseMg: raw.bsa_calc.per_dose_mg,
    } : null,
    usageUnverified: raw.usage_unverified ?? false,
    isRsa: raw.is_rsa ?? null,
    rsaType: raw.rsa_type ?? null,
    rsaNote: raw.rsa_note ?? null,
    rsaSource: raw.rsa_source ?? null,

    patentStatus: raw.patent_status ?? null,
    patentExpiryDate: raw.patent_expiry_date ?? null,
    patentLoeDateInferred: raw.patent_loe_date_inferred ?? null,
    patentSource: raw.patent_source ?? null,
    patentSourceNote: raw.patent_source_note ?? null,
    patentSubstancePatents: raw.patent_substance_patents ?? [],
    patentSecondaryPatents: raw.patent_secondary_patents ?? [],
    mfdsPermit: raw.mfds_permit ? {
      itemSeq: raw.mfds_permit.item_seq,
      itemEngName: raw.mfds_permit.item_eng_name,
      permitHolder: raw.mfds_permit.permit_holder,
      permitDate: raw.mfds_permit.permit_date,
      cancelStatus: raw.mfds_permit.cancel_status,
      etcOtc: raw.mfds_permit.etc_otc,
      atcCode: raw.mfds_permit.atc_code,
      mainIngr: raw.mfds_permit.main_ingr,
      mainIngrEng: raw.mfds_permit.main_ingr_eng,
      materialName: raw.mfds_permit.material_name,
      chart: raw.mfds_permit.chart,
      packUnit: raw.mfds_permit.pack_unit,
      validTerm: raw.mfds_permit.valid_term,
      storageMethod: raw.mfds_permit.storage_method,
      rareDrugYn: raw.mfds_permit.rare_drug_yn,
      newdrugClass: raw.mfds_permit.newdrug_class,
      reexamTarget: raw.mfds_permit.reexam_target,
      reexamDate: raw.mfds_permit.reexam_date,
      changeDate: raw.mfds_permit.change_date,
      permitKind: raw.mfds_permit.permit_kind,
      effectText: raw.mfds_permit.effect_text,
      cautionText: raw.mfds_permit.caution_text,
      udDocUrl: raw.mfds_permit.ud_doc_url,
      eeDocUrl: raw.mfds_permit.ee_doc_url,
      nbDocUrl: raw.mfds_permit.nb_doc_url,
    } : null,

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
      enrichmentSource: p.enrichment_source ?? null,
      bsaCalc: p.bsa_calc ? {
        method: p.bsa_calc.method,
        rationale: p.bsa_calc.rationale,
        intervalDays: p.bsa_calc.interval_days,
        perDoseMg: p.bsa_calc.per_dose_mg,
      } : null,
      usageUnverified: p.usage_unverified ?? false,
      isRsa: p.is_rsa ?? null,
      rsaType: p.rsa_type ?? null,
      rsaNote: p.rsa_note ?? null,
      rsaSource: p.rsa_source ?? null,
      normalizedName: p.normalized_name,
      insuranceCode: p.insurance_code,
    });
  }
  return out;
}

export function exportDomesticPriceChangesUrl(query: string, format: 'xlsx' | 'csv' = 'xlsx'): string {
  return `/api/domestic/price-changes/export?q=${encodeURIComponent(query)}&format=${format}`;
}

export interface EnrichmentResult {
  normalized_name: string;
  is_failure?: boolean;
  approval_date?: string | null;
  usage_text?: string;
  daily_dose_units?: number | null;
  dose_schedule?: string | null;
  cycle_days?: number | null;
  doses_per_cycle?: number | null;
  confidence?: string;
  notes?: string;
  treatment_cost?: { daily?: number | null; monthly?: number | null; annual?: number | null; note?: string };
  cache_source?: string;
}

export interface EnrichmentRequestItem {
  normalized_name: string;
  product_name?: string;
  ingredient?: string;
  current_price?: number;
  code?: string;
  codes?: string[];
}

/** 기준약제 + 비교약제들을 한 번에 enrich. 허가일·용법·정확한 daily_cost 비동기 채움. */
export async function enrichBulk(items: EnrichmentRequestItem[]): Promise<Record<string, EnrichmentResult>> {
  if (items.length === 0) return {};
  const res = await api.post<{ enrichments: Record<string, EnrichmentResult> }>(
    '/api/domestic/enrichment-bulk',
    { items: items.slice(0, 10) },
  );
  return res.enrichments || {};
}

export interface ChangeReasonReference {
  title?: string;
  url: string;
  media?: string;
  journal?: string;         // v1 호환
  weight?: number;
  published_at?: string;
  date_unknown?: boolean;
  notes?: string;
}

export interface ChangeReasonAnalysisMeta {
  source?: string;
  total_articles?: number;
  tier_a_count?: number;
  tier_b_count?: number;
  tier_c_count?: number;
  pubmed_count?: number;          // v1 호환
  kr_count?: number;              // v1 호환
  ma_journal_count?: number;      // v1 호환
  detected_mechanisms?: string[];
  top_media?: { media: string; weight: number }[];
}

export interface ChangeReasonResult {
  mechanism: string;
  mechanism_label: string;
  reason: string;
  confidence: string;
  evidence_summary?: string;
  references?: ChangeReasonReference[];
  analysis_meta?: ChangeReasonAnalysisMeta;
  notes?: string;
  cached?: boolean;
  window?: { from?: string; to?: string; months?: number };
  review?: { approved?: boolean; final_verdict?: string };
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
