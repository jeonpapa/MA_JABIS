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

export interface ApprovalRecord {
  approved: boolean;
  date: string | null;
  indication: string | null;
  fullIndication: string | null;
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
}

interface RawForeignCachedResponse {
  query: string;
  results: Record<string, RawPricingEntry[]>;
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

// ─────────────────────────────────────────────────────────────────────────────
// country key ↔ server country code 매핑
// ─────────────────────────────────────────────────────────────────────────────

// UI key → server country code (ISO-2)
const PRICING_COUNTRY_CODE: Record<string, string> = {
  usa: 'US', uk: 'UK', germany: 'DE', france: 'FR',
  canada: 'CA', japan: 'JP', italy: 'IT', switzerland: 'CH',
};

// UI key → approval agency
const APPROVAL_AGENCY: Record<string, string | null> = {
  usa: 'FDA',
  uk: 'MHRA',
  germany: 'EMA',
  france: 'EMA',
  italy: 'EMA',
  canada: null,      // Health Canada — 데이터 소스 미구현
  japan: 'PMDA',
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

async function fetchPricing(query: string): Promise<{
  a8Pricing: Record<string, A8Pricing | undefined>;
  a8PricingByForm: Record<string, A8Pricing[]>;
  productName: string;
  ingredient: string;
  lastSearchedAt: string;
}> {
  const res = await api.get<RawForeignCachedResponse>(
    `/api/foreign/cached?q=${encodeURIComponent(query)}`,
  );
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
  return {
    a8Pricing, a8PricingByForm, productName, ingredient,
    lastSearchedAt: toIsoDate(lastSearchedAt),
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
    const res = await api.get<RawApprovalMatrix>(
      `/api/approval/matrix?product=${encodeURIComponent(query)}`,
    );
    // 각 agency 가 커버하는 disease + rows 수
    const byAgency: Record<string, { diseases: Set<string>; rows: number }> = {};
    for (const row of res.rows || []) {
      for (const agency of row.agencies || []) {
        const entry = byAgency[agency] || { diseases: new Set(), rows: 0 };
        entry.diseases.add(row.disease);
        entry.rows += 1;
        byAgency[agency] = entry;
      }
    }
    for (const [uiKey, agency] of Object.entries(APPROVAL_AGENCY)) {
      if (!agency) {
        out[uiKey] = undefined;
        continue;
      }
      const entry = byAgency[agency];
      if (!entry || entry.rows === 0) {
        out[uiKey] = { approved: false, date: null, indication: null, fullIndication: null };
        continue;
      }
      const diseases = Array.from(entry.diseases).sort();
      out[uiKey] = {
        approved: true,
        date: null, // matrix 에 per-agency 최초 허가일 없음 — detail API 필요. Phase 3 확장
        indication: `${entry.rows}개 적응증 (${diseases.slice(0, 3).join(', ')}${diseases.length > 3 ? ' 외' : ''})`,
        fullIndication: `${agency} 승인 적응증 ${entry.rows}건 — ${diseases.join(', ')}`,
      };
    }
  } catch {
    for (const uiKey of Object.keys(APPROVAL_AGENCY)) {
      out[uiKey] = undefined;
    }
  }
  return out;
}

export async function fetchForeignDrugDetail(query: string): Promise<ForeignDrugDetail> {
  const [pricing, hta, approval] = await Promise.all([
    fetchPricing(query),
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
