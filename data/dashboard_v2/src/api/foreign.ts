import { api } from './client';

// ─────────────────────────────────────────────────────────────────────────────
// UI 타입 (international-pricing 페이지가 기대하는 형태)
// ─────────────────────────────────────────────────────────────────────────────

export interface ForeignDrugListItem {
  id: string;
  queryName: string;
  canonical?: string;
  aliases?: string[];
  lastSearchedAt: string;
  countryCount: number;
  hasPrice: boolean;
}

export type FormType = 'oral' | 'injection' | 'unknown';

export interface A8Pricing {
  price: number;
  currency: string;
  reimbursed: boolean;
  reimbursedDate: string;
  note: string;
  sourceLabel?: string;
  sourceUrl?: string;
  krwConverted?: number;
  adjustedPriceKrw?: number;
  formType: FormType;
  productName?: string;
  dosageStrength?: string;
  dosageForm?: string;
  dosageStrengthMg?: number;
  dailyDoseMg?: number;
  dailyCostKrw?: number;
  dosingScheduleLabel?: string;
  packCount?: number;
  perUnitLocal?: number;
  packageUnit?: string;
}

export interface HtaRecord {
  status: string;        // "권고" | "조건부 권고" | "비권고" | "종료" | "검토 중"
  htaBody: string;       // "NICE" | "CADTH" | "PBAC" | "SMC"
  date: string;
  recommendation: string;
  note: string;
  fullText: string;
  detailUrl?: string;
}

export type ApprovalDateSource = 'official' | 'unverified' | null;

export interface ApprovalIndicationBlock {
  title: string;
  approvalDate: string | null;
  dateSource?: ApprovalDateSource;
  biomarkerLabel: string | null;
  combinationLabel: string | null;
  labelUrl: string | null;
  body: string;
  /** anchor chips — disease + stage + LoT + biomarker_class (카드 헤더 스캔용) */
  disease?: string | null;
  stage?: string | null;
  lineOfTherapy?: string | null;
  biomarkerClass?: string | null;
  indicationId?: string;
}

export interface ApprovalRecord {
  approved: boolean;
  date: string | null;
  dateSource?: ApprovalDateSource;
  indication: string | null;
  fullIndication: string | null;
  indicationBlocks?: ApprovalIndicationBlock[];
}

export interface ForeignDrugDetail {
  id: string;
  productName: string;
  ingredient: string;
  searchedAt: string;
  searchedBy: string;
  /** 필터 적용 후의 국가별 대표 1건 (기본 view). */
  a8Pricing: Record<string, A8Pricing | undefined>;
  /** 국가별 전체 제형 가격 리스트 (필터 전). */
  a8PricingByForm: Record<string, A8Pricing[]>;
  htaStatus: Record<string, HtaRecord | undefined>;
  approvalStatus: Record<string, ApprovalRecord | undefined>;
  /** 국가별 빈 결과의 정책 메타 (예: FR PPH 비공개 / CA pCPA) */
  coverageNotes?: Record<string, CoverageNote>;
}

// ─────────────────────────────────────────────────────────────────────────────
// Raw server 타입
// ─────────────────────────────────────────────────────────────────────────────

interface RawForeignDrug {
  query_name: string;
  canonical?: string;
  aliases?: string[];
  last_searched_at: string;
  country_count: number;
  has_price: number;
}

interface RawPricingEntry {
  country: string;
  currency: string;
  local_price: number | null;
  product_name: string;
  ingredient?: string;
  searched_at: string;
  source_label?: string;
  source_url?: string;
  krw_converted?: number | null;
  adjusted_price_krw?: number | null;
  raw_data?: string;
  form_type?: string | null;
  dosage_strength?: string | null;
  dosage_form?: string | null;
  dosage_strength_mg?: number | null;
  daily_dose_mg?: number | null;
  daily_cost_krw?: number | null;
  dosing_schedule_label?: string | null;
  pack_count?: number | null;
  per_unit_local?: number | null;
  package_unit?: string | null;
}

interface RawCoverageNote {
  policy: string;
  public_db_has_price?: boolean | string;
  source_hint?: string;
  requires_auth?: boolean;
}

interface RawForeignCachedResponse {
  query: string;
  results: Record<string, RawPricingEntry[]>;
  coverage_notes?: Record<string, RawCoverageNote>;
}

export interface CoverageNote {
  policy: string;
  publicDbHasPrice?: boolean | string;
  sourceHint?: string;
  requiresAuth?: boolean;
}

interface RawHtaResult {
  body: string;
  country: string;
  decision: string;
  decision_date: string | null;
  detail_url: string | null;
  drug_query: string;
  title?: string;
  indication?: string;
  extra?: Record<string, unknown>;
  pdf_url?: string;
}

interface RawHtaResponse {
  drug: string;
  available_bodies: string[];
  count: number;
  results: RawHtaResult[];
}

interface RawApprovalRow {
  agencies: string[];
  biomarker_class: string;
  disease: string;
  indication_id: string;
  line_of_therapy: string;
  pivotal_trial: string | null;
  stage: string;
}

interface RawApprovalMatrix {
  product: string;
  totals: Record<string, number>;
  rows: RawApprovalRow[];
  by_disease: Array<Record<string, number | string>>;
}

interface RawApprovalFullTextRow {
  indication_id: string;
  disease: string | null;
  stage: string | null;
  line_of_therapy: string | null;
  biomarker_class: string | null;
  title: string | null;
  agency: string;
  approval_date: string | null;
  date_source: string | null;
  label_excerpt: string | null;
  label_full_text: string | null;
  label_url: string | null;
  biomarker_label: string | null;
  combination_label: string | null;
}

interface RawApprovalFullTextResponse {
  product: string;
  by_agency: Record<string, RawApprovalFullTextRow[]>;
}

// ─────────────────────────────────────────────────────────────────────────────
// country key ↔ server country code 매핑
// ─────────────────────────────────────────────────────────────────────────────

// UI key → server country code (ISO-2)
const PRICING_COUNTRY_CODE: Record<string, string> = {
  usa: 'US', uk: 'UK', germany: 'DE', france: 'FR',
  canada: 'CA', japan: 'JP', italy: 'IT', switzerland: 'CH',
};

// UI key → approval agency
// EU centralized procedure 는 EMA 1회 허가로 DE/FR/IT 공통 → `eu` 단일 키로 통합
const APPROVAL_AGENCY: Record<string, string | null> = {
  usa: 'FDA',
  uk: 'MHRA',
  eu: 'EMA',
  canada: null,      // Health Canada — 데이터 소스 미구현
  japan: 'PMDA',
  korea: 'MFDS',
  switzerland: null, // Swissmedic — 데이터 소스 미구현
  australia: 'TGA',
  scotland: 'MHRA',
};

const HTA_BODY_KEY: Record<string, string> = {
  NICE: 'uk',
  CADTH: 'canada',
  PBAC: 'australia',
  SMC: 'scotland',
};

// ─────────────────────────────────────────────────────────────────────────────
// 헬퍼
// ─────────────────────────────────────────────────────────────────────────────

function slugify(q: string): string {
  return q.toLowerCase().trim().replace(/\s+/g, '_');
}

function decisionStatus(decision: string): { status: string; recommendation: string } {
  const d = (decision || '').toLowerCase();
  if (d.includes('recommend') && !d.includes('not')) return { status: '권고', recommendation: '권고' };
  if (d.includes('optimized') || d.includes('restricted') || d.includes('conditional')) {
    return { status: '조건부 권고', recommendation: '조건부 권고' };
  }
  if (d.includes('not recommend') || d.includes('reject')) return { status: '비권고', recommendation: '비권고' };
  if (d.includes('terminate')) return { status: '종료', recommendation: '평가 종료' };
  if (d.includes('approved')) return { status: '권고', recommendation: '승인' };
  return { status: '검토 중', recommendation: decision || '검토 중' };
}

function toIsoDate(s: string | null | undefined): string {
  if (!s) return '';
  // "2024-05-14T..." 또는 "2024-05-14" 또는 "2024.05.14"
  const core = s.split('T')[0];
  return core.replace(/\./g, '-');
}

function mapPricingEntry(raw: RawPricingEntry): A8Pricing | undefined {
  let reimbursed = false;
  let note = '';
  try {
    const rawData = raw.raw_data ? JSON.parse(raw.raw_data) : {};
    const slStatus = (rawData.sl_status || '').toString().toLowerCase();
    if (slStatus.includes('sl') || slStatus.includes('remboursement') || slStatus.includes('erstattung')) {
      reimbursed = true;
    }
    note = rawData.company || '';
  } catch {
    // ignore
  }
  const ft = (raw.form_type || '').toLowerCase();
  const formType: FormType = ft === 'oral' || ft === 'injection' ? ft : 'unknown';

  // null 가격도 표시: 가격 미공개/비급여
  const price = raw.local_price ?? 0;
  const isNullPrice = raw.local_price == null;

  return {
    price,
    currency: raw.currency,
    reimbursed: isNullPrice ? false : reimbursed,
    reimbursedDate: '',
    note: isNullPrice ? '(가격 미공개)' : note,
    sourceLabel: raw.source_label,
    sourceUrl: raw.source_url,
    krwConverted: raw.krw_converted ?? undefined,
    adjustedPriceKrw: raw.adjusted_price_krw ?? undefined,
    formType,
    productName: raw.product_name,
    dosageStrength: raw.dosage_strength || undefined,
    dosageForm: raw.dosage_form || undefined,
    dosageStrengthMg: raw.dosage_strength_mg ?? undefined,
    dailyDoseMg: raw.daily_dose_mg ?? undefined,
    dailyCostKrw: raw.daily_cost_krw ?? undefined,
    dosingScheduleLabel: raw.dosing_schedule_label ?? undefined,
    packCount: raw.pack_count ?? undefined,
    perUnitLocal: raw.per_unit_local ?? undefined,
    packageUnit: raw.package_unit || undefined,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Public API
// ─────────────────────────────────────────────────────────────────────────────

export async function fetchForeignDrugList(): Promise<ForeignDrugListItem[]> {
  const raw = await api.get<RawForeignDrug[]>('/api/foreign/drugs');
  return raw
    .filter(r => r.has_price > 0)
    .map(r => ({
      id: slugify(r.canonical || r.query_name),
      queryName: r.query_name,
      canonical: r.canonical,
      aliases: r.aliases,
      lastSearchedAt: toIsoDate(r.last_searched_at),
      countryCount: r.country_count,
      hasPrice: r.has_price > 0,
    }));
}

async function fetchPricing(query: string, useCache: boolean = true): Promise<{
  a8Pricing: Record<string, A8Pricing | undefined>;
  a8PricingByForm: Record<string, A8Pricing[]>;
  productName: string;
  ingredient: string;
  lastSearchedAt: string;
  coverageNotes: Record<string, CoverageNote>;
}> {
  const url = `/api/foreign/cached?q=${encodeURIComponent(query)}&use_cache=${useCache}`;
  const res = await api.get<RawForeignCachedResponse>(url);
  const a8Pricing: Record<string, A8Pricing | undefined> = {};
  const a8PricingByForm: Record<string, A8Pricing[]> = {};
  let productName = query;
  let ingredient = '';
  let lastSearchedAt = '';
  for (const [uiKey, code] of Object.entries(PRICING_COUNTRY_CODE)) {
    const list = res.results[code] || [];
    // 최신 항목 우선
    const sorted = [...list].sort((a, b) =>
      (b.searched_at || '').localeCompare(a.searched_at || ''));
    // 제형별 최신 1건씩
    const perForm: Record<FormType, A8Pricing | undefined> = {
      oral: undefined, injection: undefined, unknown: undefined,
    };
    for (const entry of sorted) {
      const mapped = mapPricingEntry(entry);
      if (!mapped) continue;
      if (!perForm[mapped.formType]) perForm[mapped.formType] = mapped;
      if (!ingredient && entry.ingredient) ingredient = entry.ingredient;
      if (!lastSearchedAt || entry.searched_at > lastSearchedAt) lastSearchedAt = entry.searched_at;
      if (!productName || productName === query) productName = entry.product_name || productName;
    }
    const forms: A8Pricing[] = [];
    if (perForm.oral)      forms.push(perForm.oral);
    if (perForm.injection) forms.push(perForm.injection);
    if (perForm.unknown)   forms.push(perForm.unknown);
    a8PricingByForm[uiKey] = forms;
    // 기본 view: oral → injection → unknown 순으로 첫 비어있지 않은 것
    a8Pricing[uiKey] = forms[0];
  }
  // coverage_notes 를 code(ISO-2) → uiKey 로 변환해 컴포넌트에서 바로 사용 가능하게 함
  const coverageNotes: Record<string, CoverageNote> = {};
  const codeToUiKey: Record<string, string> = {};
  for (const [uiKey, code] of Object.entries(PRICING_COUNTRY_CODE)) {
    codeToUiKey[code] = uiKey;
  }
  for (const [code, note] of Object.entries(res.coverage_notes || {})) {
    const uiKey = codeToUiKey[code] || code.toLowerCase();
    coverageNotes[uiKey] = {
      policy: note.policy,
      publicDbHasPrice: note.public_db_has_price,
      sourceHint: note.source_hint,
      requiresAuth: note.requires_auth,
    };
  }
  return {
    a8Pricing, a8PricingByForm, productName, ingredient,
    lastSearchedAt: toIsoDate(lastSearchedAt),
    coverageNotes,
  };
}

async function fetchHta(query: string): Promise<Record<string, HtaRecord | undefined>> {
  const htaStatus: Record<string, HtaRecord | undefined> = {};
  try {
    const res = await api.get<RawHtaResponse>(
      `/api/hta/approvals?drug=${encodeURIComponent(query)}`,
    );
    // body 별로 최신 결과 하나
    const latestByBody: Record<string, RawHtaResult> = {};
    for (const r of res.results || []) {
      const prev = latestByBody[r.body];
      if (!prev || (r.decision_date || '') > (prev.decision_date || '')) {
        latestByBody[r.body] = r;
      }
    }
    for (const [body, rec] of Object.entries(latestByBody)) {
      const key = HTA_BODY_KEY[body];
      if (!key) continue;
      const { status, recommendation } = decisionStatus(rec.decision);
      htaStatus[key] = {
        status,
        htaBody: body,
        date: toIsoDate(rec.decision_date),
        recommendation,
        note: rec.title || rec.indication || '',
        fullText: rec.title || rec.indication || '',
        detailUrl: rec.detail_url || undefined,
      };
    }
  } catch {
    // HTA 없으면 조용히 빈 값
  }
  return htaStatus;
}

async function fetchApprovalByCountry(
  query: string,
): Promise<Record<string, ApprovalRecord | undefined>> {
  const out: Record<string, ApprovalRecord | undefined> = {};
  try {
    // 원문 허가 문구 (agency 별) 우선 시도 — 카드에 실제 라벨 원문 노출용
    const ft = await api.get<RawApprovalFullTextResponse>(
      `/api/approval/full_text?product=${encodeURIComponent(query)}`,
    );
    const byAgency = ft.by_agency || {};
    for (const [uiKey, agency] of Object.entries(APPROVAL_AGENCY)) {
      if (!agency) {
        out[uiKey] = undefined;
        continue;
      }
      const rows = byAgency[agency] || [];
      if (rows.length === 0) {
        out[uiKey] = { approved: false, date: null, dateSource: null, indication: null, fullIndication: null, indicationBlocks: [] };
        continue;
      }
      // 승인일 최신순 정렬 — null(미상) 은 맨 뒤
      const sortedRows = [...rows].sort((a, b) => {
        const da = a.approval_date || '';
        const db = b.approval_date || '';
        if (!da && !db) return 0;
        if (!da) return 1;
        if (!db) return -1;
        return db.localeCompare(da);
      });
      // 최초 허가일 = 가장 이른 approval_date
      const dates = sortedRows.map(r => r.approval_date).filter((d): d is string => !!d).sort();
      const firstDate = dates[0] || null;
      const latestDate = dates[dates.length - 1] || null;
      const diseases = Array.from(new Set(sortedRows.map(r => r.disease).filter(Boolean) as string[])).sort();
      const normalizeDateSource = (raw: string | null, ag: string): ApprovalDateSource => {
        const v = (raw || '').toLowerCase();
        if (v === 'mfds_official' || v === 'official') return 'official';
        if (v === 'unverified_estimate' || v === 'unverified') return 'unverified';
        // MFDS 는 date_source 필수: null 이면 '추정'으로 간주
        if (ag === 'MFDS') return 'unverified';
        // 나머지 agency 는 공식 소스에서 직접 수집 → 'official'
        return 'official';
      };
      const indicationBlocks: ApprovalIndicationBlock[] = sortedRows.map(r => ({
        title: r.title || r.disease || '(제목 없음)',
        approvalDate: r.approval_date ? toIsoDate(r.approval_date) : null,
        dateSource: normalizeDateSource(r.date_source, r.agency),
        biomarkerLabel: r.biomarker_label || null,
        combinationLabel: r.combination_label || null,
        labelUrl: r.label_url || null,
        body: r.label_full_text || r.label_excerpt || '(원문 없음)',
        disease: r.disease || null,
        stage: r.stage || null,
        lineOfTherapy: r.line_of_therapy || null,
        biomarkerClass: r.biomarker_class || null,
        indicationId: r.indication_id,
      }));
      const recordDateSource: ApprovalDateSource = indicationBlocks.some(b => b.dateSource === 'unverified')
        ? (indicationBlocks.every(b => b.dateSource === 'unverified') ? 'unverified' : 'unverified')
        : (indicationBlocks.some(b => b.dateSource === 'official') ? 'official' : null);
      const fullIndicationText = indicationBlocks.map(b => {
        const header = [
          b.title,
          b.approvalDate ? `승인 ${b.approvalDate}` : null,
          b.biomarkerLabel,
          b.combinationLabel,
          b.labelUrl,
        ].filter(Boolean).join(' · ');
        return `■ ${header}\n${b.body}`;
      }).join('\n\n');
      out[uiKey] = {
        approved: true,
        date: latestDate ? toIsoDate(latestDate) : (firstDate ? toIsoDate(firstDate) : null),
        dateSource: recordDateSource,
        indication: `${sortedRows.length}개 적응증 (${diseases.slice(0, 3).join(', ')}${diseases.length > 3 ? ' 외' : ''})`,
        fullIndication: fullIndicationText,
        indicationBlocks,
      };
    }
  } catch {
    for (const uiKey of Object.keys(APPROVAL_AGENCY)) {
      out[uiKey] = undefined;
    }
  }
  return out;
}

export async function fetchForeignDrugDetail(query: string, useCache: boolean = true): Promise<ForeignDrugDetail> {
  const [pricing, hta, approval] = await Promise.all([
    fetchPricing(query, useCache),
    fetchHta(query),
    fetchApprovalByCountry(query),
  ]);
  return {
    id: slugify(query),
    productName: pricing.productName || query,
    ingredient: pricing.ingredient,
    searchedAt: pricing.lastSearchedAt,
    searchedBy: '-',
    a8Pricing: pricing.a8Pricing,
    a8PricingByForm: pricing.a8PricingByForm,
    htaStatus: hta,
    approvalStatus: approval,
    coverageNotes: pricing.coverageNotes,
  };
}

export async function searchForeignLive(
  query: string,
  countries?: string[],
): Promise<void> {
  await api.post('/api/foreign/search', {
    query,
    countries: countries ?? undefined,
    use_cache: false,
  });
}

export async function deleteForeignDrug(queryName: string): Promise<{ ok: boolean; deleted: number }> {
  return api.delete<{ ok: boolean; deleted: number; query_name: string }>(
    `/api/foreign/drugs/${encodeURIComponent(queryName)}`,
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// /api/foreign/country-overview — 국가별 카드 그리드 (pure-napping-goose Phase 5)
// ─────────────────────────────────────────────────────────────────────────────

export type ReimbursementSummary =
  | 'recommend' | 'restrict' | 'optimised'
  | 'reject' | 'not_listed' | 'not_applicable' | 'none';

export interface CountryOverviewIndication {
  indication_id: string;
  title: string | null;
  disease: string | null;
  line_of_therapy: string | null;
  biomarker: string | null;
  approval_date: string | null;
  label_excerpt: string | null;
  label_url: string | null;
  reimbursement: {
    decision_type: string | null;
    decision_id: string | null;
    decision_date: string | null;
    criteria_text: string | null;
    source_url: string | null;
    body: string | null;
  } | null;
  price: {
    currency: string | null;
    local_price: number | null;
    adjusted_price_krw: number | null;
    daily_cost_krw: number | null;
    form_type: string | null;
    dosage_strength: string | null;
    package_unit: string | null;
    source_label: string | null;
    searched_at: string | null;
  } | null;
}

export interface CountryOverviewCard {
  country: 'US' | 'EU' | 'UK' | 'JP' | 'AU' | 'KR';
  agency: string | null;        // FDA / EMA / MHRA / PMDA / TGA / MFDS
  body: string | null;          // CMS / NICE / CHUIKYO / PBAC / HIRA
  currency_hint: string | null;
  approval_count: number;
  indications: CountryOverviewIndication[];
  reimbursement_summary: ReimbursementSummary;
  reimbursement_count: number;
  price_summary: CountryOverviewIndication['price'];
}

export interface CountryOverviewResponse {
  product: string;
  inn: string | null;
  query: string;
  countries: CountryOverviewCard[];
}

export async function fetchCountryOverview(query: string): Promise<CountryOverviewResponse> {
  return api.get<CountryOverviewResponse>(
    `/api/foreign/country-overview?query=${encodeURIComponent(query.trim())}`,
  );
}
