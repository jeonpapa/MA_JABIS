import { api } from './client';

// ─────────────────────────────────────────────────────────────────────────────
// 해외약가 (International Pricing) API 모듈
//
// 데이터 소스 (모두 캐시/DB 조회 — 페이지에서 라이브 스크레이프 금지):
//   GET /api/foreign/drugs                       검색 이력 카드
//   GET /api/foreign/cached?q=<name>             A8 국가 캐시 가격 (use_cache 기본 true)
//   GET /api/foreign/country-overview?query=...  국가별 허가×급여×가격 (급여 배지 도출용)
//   GET /api/hta/approvals?drug=<name>           NICE/CADTH/PBAC/SMC (캐시 우선, miss 시 백엔드가 1회 수집)
//   GET /api/approval/full_text?product=<slug>   6-agency (FDA/EMA/PMDA/MFDS/MHRA/TGA) 허가 원문
//   POST /api/foreign/search                     라이브 스크레이프 — 명시적 사용자 액션 전용 (수 분 소요)
//
// 데이터 정직성 원칙 (CLAUDE.md):
//   - 가격/일자/급여상태 절대 임의 생성 금지. 없으면 '정보 없음'.
//   - 급여 배지는 (a) raw_data.sl_status 명시 신호 또는 (b) country-overview 의
//     positive reimbursement (recommend/restrict/optimised) 에서만 도출.
//     그 외에는 reimbursedKnown=false → '급여정보 없음' 렌더.
//   - US 가격은 백엔드가 WAC 기준으로 저장 (AWP 금지) — 표시만 한다.
// ─────────────────────────────────────────────────────────────────────────────

// ── UI 타입 ──────────────────────────────────────────────────────────────────

export interface ForeignDrugListItem {
  id: string;              // query_name (= product slug: keytruda, welireg …)
  queryName: string;
  canonical: string;       // INN (pembrolizumab …)
  aliases: string[];
  lastSearchedAt: string;  // YYYY-MM-DD
  countryCount: number;
  hasPrice: boolean;
}

export type FormType = 'oral' | 'injection' | 'unknown';

/** 조정가 산출 과정 — 사후관리 패널용 (서버 PriceCalculator 중간값 그대로, 프론트 재계산 금지) */
export interface AdjCalcBreakdown {
  listedPrice: number;          // 표시가 (pack 단위 국가는 pack 가격)
  packCount: number;
  perUnitLocal: number;         // 표시가 / pack_count
  exchangeRate: number;         // KEB 36개월 평균 매매기준율
  fxFrom: string;               // 환율 산정 시작월 (YYYYMMDD)
  fxTo: string;                 // 환율 산정 종료월
  krwConverted?: number;        // per-unit × FX
  factoryRatio?: number;        // 국가별 공장도 출하 비율
  factoryRatioLabel?: string;
  factoryPriceKrw?: number;     // × factory_ratio
  vatRate?: number;             // KR VAT 0.10 (한국 기준 상수)
  vatAppliedKrw?: number;       // × 1.10
  distributionMargin?: number;  // KR 유통거래폭 0.0869
  adjustedPriceKrw: number;     // 최종 per-unit 조정가
}

export interface A8Pricing {
  /** local_price 그대로 (null = 비공개/로그인월 — 임의 값 금지) */
  price: number | null;
  currency: string;
  /** 급여 신호가 명시적으로 확인된 경우에만 true */
  reimbursed: boolean;
  /** 급여 여부를 판단할 근거 데이터가 있었는지. false → '급여정보 없음' */
  reimbursedKnown: boolean;
  /** country-overview HTA/급여 결정일 (등재일 아님 — 라벨에 명시) */
  reimbursedDate: string;
  reimbursedLabel: string;     // '권고' | '조건부' | ''
  note: string;                // 가격 미공개 사유 등
  formType: FormType;
  productName: string;
  dosageStrength?: string;
  sourceLabel?: string;
  sourceUrl?: string;
  adjustedPriceKrw?: number;   // A8 조정가 (per-unit KRW)
  dailyCostKrw?: number;
  dosingScheduleLabel?: string;
  packCount?: number;
  searchedAt: string;
  /** 해당 국가 캐시 행 수 (대표 1건 외 변형 수) */
  variantCount: number;
  /** 조정가 산출 과정 (가격·환율 모두 있을 때만) — 사후관리 패널용 */
  calc?: AdjCalcBreakdown;
}

/** A8 조정가 요약 — 가격 보유 국가 기준 min/max/avg + 제외국 명시 */
export interface A8Summary {
  minKrw: number;
  minCountryKey: string;        // uiKey (usa/uk/…)
  maxKrw: number;
  maxCountryKey: string;
  avgKrw: number;
  includedKeys: string[];       // 평균 산출에 포함된 uiKey
  excludedKeys: string[];       // 조정가 없어 제외된 uiKey
}

export interface CoverageNote {
  policy: string;
  sourceHint?: string;
  requiresAuth?: boolean;
}

export interface PricingTabData {
  productName: string;
  ingredient: string;
  lastSearchedAt: string;
  /** uiKey(usa/uk/germany/…) → 대표 1건. 없으면 undefined → '정보 없음' */
  a8Pricing: Record<string, A8Pricing | undefined>;
  coverageNotes: Record<string, CoverageNote>;
  hasAnyPrice: boolean;
  /** 조정가 보유 국가가 1개 이상일 때만 — min/max/avg 카드용 */
  summary?: A8Summary;
}

export interface HtaDecisionItem {
  decision: string;
  status: string;          // 권고 | 조건부 권고 | 비권고 | 종료 | 원문 참조 | 검토 중
  recommendation: string;
  date: string;
  title: string;
  indication: string;
  detailUrl?: string;
}

export interface HtaRecord {
  status: string;
  htaBody: string;
  date: string;
  recommendation: string;
  note: string;
  fullText: string;
  detailUrl?: string;
  /** 해당 body 의 전체 평가 이력 (최신순) — 확장 패널 목록용 */
  allDecisions: HtaDecisionItem[];
  totalCount: number;
}

export type HtaTabData = Record<string, HtaRecord | undefined>;

export type ApprovalDateSource = 'official' | 'unverified';

export interface ApprovalIndicationBlock {
  title: string;
  approvalDate: string | null;
  dateSource: ApprovalDateSource;
  biomarkerLabel: string | null;
  combinationLabel: string | null;
  labelUrl: string | null;
  body: string;            // 허가 원문 (label_full_text)
  disease: string | null;
  lineOfTherapy: string | null;
  indicationId: string;
}

export interface AgencyApproval {
  /** DB 에 허가 row 존재 여부. false ≠ 미허가 — '허가 정보 없음' 으로 렌더 (단정 금지) */
  hasData: boolean;
  firstApprovalDate: string | null;  // 가장 이른 approval_date
  latestApprovalDate: string | null;
  /** 일부 일자가 추정(unverified) 인지 */
  hasUnverifiedDates: boolean;
  indicationSummary: string;         // "N개 적응증 (A, B, C 외)"
  indicationBlocks: ApprovalIndicationBlock[];
}

export type ApprovalTabData = Record<string, AgencyApproval>; // key: FDA/EMA/PMDA/MFDS/MHRA/TGA

// ── Raw 서버 타입 ─────────────────────────────────────────────────────────────

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
  adjusted_price_krw?: number | null;
  daily_cost_krw?: number | null;
  dosing_schedule_label?: string | null;
  raw_data?: string;
  form_type?: string | null;
  dosage_strength?: string | null;
  pack_count?: number | null;
  // 조정가 산출 중간값 (서버 get_cached_results 가 PriceCalculator 로 재계산해 내려줌)
  exchange_rate?: number | null;
  exchange_rate_from?: string | null;
  exchange_rate_to?: string | null;
  per_unit_local?: number | null;
  krw_converted?: number | null;
  factory_ratio?: number | null;
  factory_ratio_label?: string | null;
  factory_price_krw?: number | null;
  vat_rate?: number | null;
  vat_applied_krw?: number | null;
  distribution_margin?: number | null;
}

interface RawCoverageNote {
  policy: string;
  public_db_has_price?: boolean | string;
  source_hint?: string;
  requires_auth?: boolean;
}

interface RawCachedResponse {
  query: string;
  results: Record<string, RawPricingEntry[]>;
  coverage_notes?: Record<string, RawCoverageNote>;
}

interface RawHtaResult {
  body: string;
  country: string;
  decision: string;
  decision_date: string | null;
  detail_url: string | null;
  title?: string;
  indication?: string;
}

interface RawHtaResponse {
  drug: string;
  available_bodies: string[];
  count: number;
  results: RawHtaResult[];
}

interface RawFullTextRow {
  indication_id: string;
  disease: string | null;
  line_of_therapy: string | null;
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

interface RawFullTextResponse {
  product: string;
  by_agency: Record<string, RawFullTextRow[]>;
}

interface RawCountryOverviewIndication {
  reimbursement: {
    decision_type: string | null;
    decision_date: string | null;
  } | null;
}

interface RawCountryOverviewCard {
  country: string; // US | EU | UK | JP | AU | KR
  reimbursement_summary: string;
  indications: RawCountryOverviewIndication[];
}

interface RawCountryOverviewResponse {
  product: string;
  inn: string | null;
  countries: RawCountryOverviewCard[];
}

// ── 매핑 상수 ─────────────────────────────────────────────────────────────────

/** UI key → /api/foreign/cached 의 ISO-2 국가코드 */
export const PRICING_COUNTRY_CODE: Record<string, string> = {
  usa: 'US', uk: 'UK', germany: 'DE', france: 'FR',
  canada: 'CA', japan: 'JP', italy: 'IT', switzerland: 'CH',
};

/** country-overview 국가코드 → A8 UI key (A8 에 존재하는 국가만) */
const OVERVIEW_TO_A8: Record<string, string> = { US: 'usa', UK: 'uk', JP: 'japan' };

/** 허가 탭 — 실데이터가 존재하는 6개 규제기관 */
export const APPROVAL_AGENCIES = [
  { key: 'FDA', label: '미국', flag: '🇺🇸', agencyName: 'FDA' },
  { key: 'EMA', label: '유럽연합', flag: '🇪🇺', agencyName: 'EMA' },
  { key: 'MHRA', label: '영국', flag: '🇬🇧', agencyName: 'MHRA' },
  { key: 'PMDA', label: '일본', flag: '🇯🇵', agencyName: 'PMDA' },
  { key: 'MFDS', label: '한국', flag: '🇰🇷', agencyName: 'MFDS' },
  { key: 'TGA', label: '호주', flag: '🇦🇺', agencyName: 'TGA' },
] as const;

const HTA_BODY_KEY: Record<string, string> = {
  NICE: 'uk', CADTH: 'canada', PBAC: 'australia', SMC: 'scotland',
};

// ── 헬퍼 ─────────────────────────────────────────────────────────────────────

function toIsoDate(s: string | null | undefined): string {
  if (!s) return '';
  return s.split('T')[0].replace(/\./g, '-');
}

/**
 * HTA decision 문자열 → 한국어 상태.
 * 실측 어휘 (data/db/drug_prices.db hta_approvals):
 *   NICE: Recommended | Recommended (managed access) | Not recommended | Terminated | Unknown
 *   SMC:  Accepted | Accepted (restricted) | Not recommended | Unknown
 *   PBAC: See PSD   /  CADTH: See CADTH (본문 자동수집 불가 — 링크만)
 */
export function decisionStatus(decision: string): { status: string; recommendation: string } {
  const d = (decision || '').toLowerCase();
  if (d.includes('not recommend') || d.includes('reject') || d.includes('declined')) {
    return { status: '비권고', recommendation: '비권고' };
  }
  if (d.includes('managed access') || d.includes('restrict') || d.includes('optimised')
    || d.includes('optimized') || d.includes('conditional') || d.includes('interim')) {
    return { status: '조건부 권고', recommendation: '조건부 권고' };
  }
  if (d.includes('recommend') || d.includes('accept')) {
    return { status: '권고', recommendation: '권고' };
  }
  if (d.includes('terminate') || d.includes('withdrawn')) {
    return { status: '종료', recommendation: '평가 종료' };
  }
  if (d.startsWith('see ')) {
    return { status: '원문 참조', recommendation: decision }; // See PSD / See CADTH
  }
  return { status: '검토 중', recommendation: decision || '검토 중' };
}

/** raw_data.sl_status 의 명시적 급여 등재 신호 (CH SL / FR remboursement / DE erstattung) */
function explicitReimbursedSignal(rawData: string | undefined): boolean | null {
  if (!rawData) return null;
  try {
    const parsed = JSON.parse(rawData) as Record<string, unknown>;
    const sl = String(parsed.sl_status ?? '').toLowerCase();
    if (!sl) return null;
    if (sl.includes('sl') || sl.includes('remboursement') || sl.includes('erstattung')) return true;
    return false;
  } catch {
    return null;
  }
}

function mapPricingEntry(raw: RawPricingEntry, variantCount: number): A8Pricing {
  const ft = (raw.form_type || '').toLowerCase();
  const formType: FormType = ft === 'oral' || ft === 'injection' ? ft : 'unknown';
  const explicit = explicitReimbursedSignal(raw.raw_data);
  // 산출 breakdown — 가격·환율·조정가가 전부 있을 때만 (부분 데이터로 공식 오해 방지)
  // JPY 는 KEB 가 100엔당 환율로 제공 — 서버 PriceCalculator 의 per-100 safeguard 와 동일하게
  // per-1 로 정규화해 표시 (단계 곱셈 체인이 표에서 그대로 검증되도록)
  const fxNormalized =
    raw.exchange_rate != null && raw.currency === 'JPY' && raw.exchange_rate > 100
      ? raw.exchange_rate / 100
      : raw.exchange_rate;
  const calc: AdjCalcBreakdown | undefined =
    raw.local_price != null && fxNormalized != null && raw.adjusted_price_krw != null
      ? {
          listedPrice: raw.local_price,
          packCount: raw.pack_count ?? 1,
          perUnitLocal: raw.per_unit_local ?? raw.local_price / (raw.pack_count || 1),
          exchangeRate: fxNormalized,
          fxFrom: raw.exchange_rate_from || '',
          fxTo: raw.exchange_rate_to || '',
          krwConverted: raw.krw_converted ?? undefined,
          factoryRatio: raw.factory_ratio ?? undefined,
          factoryRatioLabel: raw.factory_ratio_label ?? undefined,
          factoryPriceKrw: raw.factory_price_krw ?? undefined,
          vatRate: raw.vat_rate ?? undefined,
          vatAppliedKrw: raw.vat_applied_krw ?? undefined,
          distributionMargin: raw.distribution_margin ?? undefined,
          adjustedPriceKrw: raw.adjusted_price_krw,
        }
      : undefined;
  return {
    price: raw.local_price,
    currency: raw.currency,
    reimbursed: explicit === true,
    reimbursedKnown: explicit !== null,
    reimbursedDate: '',
    reimbursedLabel: explicit === true ? '권고' : '',
    note: raw.local_price == null ? '가격 미공개' : '',
    formType,
    productName: raw.product_name,
    dosageStrength: raw.dosage_strength || undefined,
    sourceLabel: raw.source_label,
    sourceUrl: raw.source_url,
    adjustedPriceKrw: raw.adjusted_price_krw ?? undefined,
    dailyCostKrw: raw.daily_cost_krw ?? undefined,
    dosingScheduleLabel: raw.dosing_schedule_label ?? undefined,
    packCount: raw.pack_count ?? undefined,
    searchedAt: toIsoDate(raw.searched_at),
    variantCount,
    calc,
  };
}

/**
 * 국가별 대표 1건 선택 (v2 dashboard 검증 로직 계승):
 *   1) searched_at 최신순 정렬
 *   2) 가격 있는 행(local_price != null) 우선 — 전부 null 이면 null 행 유지 (미공개 사유 표시용)
 *   3) form_type 별 최신 1건 → oral → injection → unknown 우선순위로 대표 선택
 */
function pickRepresentative(rows: RawPricingEntry[]): A8Pricing | undefined {
  if (rows.length === 0) return undefined;
  const sorted = [...rows].sort((a, b) => (b.searched_at || '').localeCompare(a.searched_at || ''));
  const priced = sorted.filter(r => r.local_price != null);
  const pool = priced.length > 0 ? priced : sorted;
  const perForm: Partial<Record<FormType, RawPricingEntry>> = {};
  for (const r of pool) {
    const ft = (r.form_type || '').toLowerCase();
    const key: FormType = ft === 'oral' || ft === 'injection' ? ft : 'unknown';
    if (!perForm[key]) perForm[key] = r;
  }
  const rep = perForm.oral ?? perForm.injection ?? perForm.unknown;
  return rep ? mapPricingEntry(rep, rows.length) : undefined;
}

const POSITIVE_REIMB = new Set(['recommend', 'restrict', 'optimised', 'optimized']);

// ── Public fetchers ──────────────────────────────────────────────────────────

/** 검색 이력 삭제 — DELETE /api/foreign/drugs/:queryName (가격·HTA·허가 캐시 전부, alias 포함) */
export async function deleteForeignDrug(queryName: string): Promise<{ deleted: number }> {
  return api.delete<{ ok: boolean; deleted: number; query_name: string }>(
    `/api/foreign/drugs/${encodeURIComponent(queryName)}`,
  );
}

/** 검색 이력 카드 — GET /api/foreign/drugs */
export async function fetchForeignDrugList(): Promise<ForeignDrugListItem[]> {
  const raw = await api.get<RawForeignDrug[]>('/api/foreign/drugs');
  return raw.map(r => ({
    id: r.query_name,
    queryName: r.query_name,
    canonical: r.canonical || r.query_name,
    aliases: r.aliases || [],
    lastSearchedAt: toIsoDate(r.last_searched_at),
    countryCount: r.country_count,
    hasPrice: r.has_price > 0,
  }));
}

/**
 * A8 급여약가 탭 — 캐시 가격 + country-overview 급여 신호 병합.
 * country-overview 실패는 가격 표시를 막지 않는다 (allSettled).
 */
export async function fetchPricingTab(query: string): Promise<PricingTabData> {
  const [cachedRes, overviewRes] = await Promise.allSettled([
    api.get<RawCachedResponse>(`/api/foreign/cached?q=${encodeURIComponent(query)}`),
    api.get<RawCountryOverviewResponse>(`/api/foreign/country-overview?query=${encodeURIComponent(query)}`),
  ]);
  if (cachedRes.status === 'rejected') throw cachedRes.reason;
  const cached = cachedRes.value;

  const a8Pricing: Record<string, A8Pricing | undefined> = {};
  let productName = '';
  let ingredient = '';
  let lastSearchedAt = '';
  let hasAnyPrice = false;

  for (const [uiKey, code] of Object.entries(PRICING_COUNTRY_CODE)) {
    const rows = cached.results?.[code] || [];
    const rep = pickRepresentative(rows);
    a8Pricing[uiKey] = rep;
    if (rep) {
      if (rep.price != null) hasAnyPrice = true;
      if (!productName) productName = rep.productName;
      if (rep.searchedAt > lastSearchedAt) lastSearchedAt = rep.searchedAt;
    }
    for (const r of rows) {
      // 짧고 깨끗한 INN 만 채택 (DE Rote Liste 의 잡음 텍스트 배제)
      const ing = (r.ingredient || '').trim();
      if (!ingredient && ing && ing.length <= 40 && !/eintrag|fachinformation/i.test(ing)) {
        ingredient = ing;
      }
    }
  }

  // country-overview 의 positive 급여 신호 병합 (US/UK/JP 만 A8 에 매핑됨)
  if (overviewRes.status === 'fulfilled') {
    const ov = overviewRes.value;
    if (!ingredient && ov.inn) ingredient = ov.inn;
    for (const card of ov.countries || []) {
      const uiKey = OVERVIEW_TO_A8[card.country];
      if (!uiKey) continue;
      const cell = a8Pricing[uiKey];
      if (!cell) continue;
      const summary = (card.reimbursement_summary || '').toLowerCase();
      if (POSITIVE_REIMB.has(summary)) {
        cell.reimbursed = true;
        cell.reimbursedKnown = true;
        cell.reimbursedLabel = summary === 'recommend' ? '권고' : '조건부';
        // positive 결정들 중 최신 decision_date (없으면 빈값 유지 — 임의 생성 금지)
        let latest = '';
        for (const ind of card.indications || []) {
          const rb = ind.reimbursement;
          if (!rb || !rb.decision_type) continue;
          if (!POSITIVE_REIMB.has(rb.decision_type.toLowerCase())) continue;
          const d = toIsoDate(rb.decision_date);
          if (d > latest) latest = d;
        }
        cell.reimbursedDate = latest;
      }
    }
  }

  const coverageNotes: Record<string, CoverageNote> = {};
  const codeToUi: Record<string, string> = {};
  for (const [uiKey, code] of Object.entries(PRICING_COUNTRY_CODE)) codeToUi[code] = uiKey;
  for (const [code, n] of Object.entries(cached.coverage_notes || {})) {
    const uiKey = codeToUi[code];
    if (!uiKey) continue;
    coverageNotes[uiKey] = { policy: n.policy, sourceHint: n.source_hint, requiresAuth: n.requires_auth };
  }

  // ── A8 조정가 요약 (min/max/avg) — per-unit adjusted_price_krw 보유 국가만 포함 ──
  const priced: { key: string; adj: number }[] = [];
  const excludedKeys: string[] = [];
  for (const uiKey of Object.keys(PRICING_COUNTRY_CODE)) {
    const adj = a8Pricing[uiKey]?.adjustedPriceKrw;
    if (adj != null && adj > 0) priced.push({ key: uiKey, adj });
    else excludedKeys.push(uiKey);
  }
  let summary: A8Summary | undefined;
  if (priced.length > 0) {
    const min = priced.reduce((a, b) => (b.adj < a.adj ? b : a));
    const max = priced.reduce((a, b) => (b.adj > a.adj ? b : a));
    summary = {
      minKrw: min.adj,
      minCountryKey: min.key,
      maxKrw: max.adj,
      maxCountryKey: max.key,
      avgKrw: Math.round(priced.reduce((s, p) => s + p.adj, 0) / priced.length),
      includedKeys: priced.map(p => p.key),
      excludedKeys,
    };
  }

  return { productName: productName || query, ingredient, lastSearchedAt, a8Pricing, coverageNotes, hasAnyPrice, summary };
}

/** HTA 현황 탭 — NICE/CADTH/PBAC/SMC. body 별 최신 1건 + 전체 이력. */
export async function fetchHtaTab(query: string): Promise<HtaTabData> {
  const res = await api.get<RawHtaResponse>(`/api/hta/approvals?drug=${encodeURIComponent(query)}`);
  const byBody: Record<string, RawHtaResult[]> = {};
  for (const r of res.results || []) {
    if (!HTA_BODY_KEY[r.body]) continue; // FDA 등 HTA 외 body 제외
    (byBody[r.body] ||= []).push(r);
  }
  const out: HtaTabData = {};
  for (const [body, rows] of Object.entries(byBody)) {
    const sorted = [...rows].sort((a, b) => (b.decision_date || '').localeCompare(a.decision_date || ''));
    const latest = sorted[0];
    const { status, recommendation } = decisionStatus(latest.decision);
    const allDecisions: HtaDecisionItem[] = sorted.map(r => {
      const ds = decisionStatus(r.decision);
      return {
        decision: r.decision,
        status: ds.status,
        recommendation: ds.recommendation,
        date: toIsoDate(r.decision_date),
        title: r.title || '',
        indication: r.indication || '',
        detailUrl: r.detail_url || undefined,
      };
    });
    out[HTA_BODY_KEY[body]] = {
      status,
      htaBody: body,
      date: toIsoDate(latest.decision_date),
      recommendation,
      note: latest.title || latest.indication || '',
      fullText: [latest.title, latest.indication].filter(Boolean).join(' — '),
      detailUrl: latest.detail_url || undefined,
      allDecisions,
      totalCount: rows.length,
    };
  }
  return out;
}

/** 허가 현황 탭 — 6-agency 허가 원문. key: FDA/EMA/PMDA/MFDS/MHRA/TGA */
export async function fetchApprovalTab(productSlug: string): Promise<ApprovalTabData> {
  const res = await api.get<RawFullTextResponse>(
    `/api/approval/full_text?product=${encodeURIComponent(productSlug.toLowerCase())}`,
  );
  const byAgency = res.by_agency || {};
  const out: ApprovalTabData = {};
  for (const ag of APPROVAL_AGENCIES) {
    const rows = byAgency[ag.key] || [];
    if (rows.length === 0) {
      out[ag.key] = {
        hasData: false, firstApprovalDate: null, latestApprovalDate: null,
        hasUnverifiedDates: false, indicationSummary: '', indicationBlocks: [],
      };
      continue;
    }
    const normalizeSource = (raw: string | null): ApprovalDateSource => {
      const v = (raw || '').toLowerCase();
      if (v === 'mfds_official' || v === 'official') return 'official';
      if (v.includes('unverified')) return 'unverified';
      // MFDS 는 date_source 필수 컬럼 — 비어있으면 추정으로 간주. 타 agency 는 공식 소스 직수집.
      return ag.key === 'MFDS' ? 'unverified' : 'official';
    };
    const sorted = [...rows].sort((a, b) => {
      const da = a.approval_date || '';
      const db = b.approval_date || '';
      if (!da && !db) return 0;
      if (!da) return 1;
      if (!db) return -1;
      return db.localeCompare(da);
    });
    const dates = sorted.map(r => r.approval_date).filter((d): d is string => !!d).sort();
    const blocks: ApprovalIndicationBlock[] = sorted.map(r => ({
      title: r.title || r.disease || '(제목 없음)',
      approvalDate: r.approval_date ? toIsoDate(r.approval_date) : null,
      dateSource: normalizeSource(r.date_source),
      biomarkerLabel: r.biomarker_label,
      combinationLabel: r.combination_label,
      labelUrl: r.label_url,
      body: r.label_full_text || r.label_excerpt || '(원문 없음)',
      disease: r.disease,
      lineOfTherapy: r.line_of_therapy,
      indicationId: r.indication_id,
    }));
    const diseases = Array.from(new Set(sorted.map(r => r.disease).filter(Boolean) as string[]));
    out[ag.key] = {
      hasData: true,
      firstApprovalDate: dates[0] ? toIsoDate(dates[0]) : null,
      latestApprovalDate: dates.length ? toIsoDate(dates[dates.length - 1]) : null,
      hasUnverifiedDates: blocks.some(b => b.dateSource === 'unverified'),
      indicationSummary: `${rows.length}개 적응증 (${diseases.slice(0, 3).join(', ')}${diseases.length > 3 ? ' 외' : ''})`,
      indicationBlocks: blocks,
    };
  }
  return out;
}

/**
 * 라이브 스크레이프 (수 분 소요·과금성) — 반드시 명시적 사용자 액션 뒤에서만 호출.
 * 페이지 로드/탭 전환에서 호출 금지.
 */
export async function searchForeignLive(query: string, countries?: string[]): Promise<void> {
  await api.post('/api/foreign/search', {
    query,
    countries: countries ?? undefined,
    use_cache: false,
  });
}
