import { api } from './client';

// ─────────────────────────────────────────────────────────────────────────────
// Home 페이지 전용 fetcher + adapter 모음.
// 서버(snake_case) 응답을 readdy 컴포넌트가 소비하는 camelCase 뷰 모델로 변환한다.
// 원칙: 서버가 주지 않는 값은 만들지 않는다 (빈 값 → 컴포넌트에서 '정보 없음' 처리).
// 색상/폰트 weight 등 시각 속성만 프런트에서 결정적(rank 기반)으로 부여한다.
// ─────────────────────────────────────────────────────────────────────────────

// ═════════════════════════════════════════════════════════════════════════════
// 1. MSD Summary  (GET /api/msd/summary)
// ═════════════════════════════════════════════════════════════════════════════

interface RawKeytrudaIndication {
  id: string;
  disease: string;
  disease_kr: string;
  line_of_therapy: string;
  stage: string;
  biomarker_class: string;
  title: string;
  pivotal_trial: string | null;
  mfds_approved: boolean;
  mfds_date: string | null;
  fda_date: string | null;
  is_reimbursed: boolean;
  reimbursement_effective_date: string | null;
  reimbursement_criteria: string | null;
  reimbursement_notice_date: string | null;
  reimbursement_notice_url: string | null;
}

interface RawMsdSummary {
  reimbursed_product_count: number;
  latest_apply_date: string | null;
  keytruda: {
    total_indications: number;
    mfds_approved: number;
    pending_mfds: number;
    reimbursed_indications: number;
    pending_reimbursement: number;
    items: RawKeytrudaIndication[];
  };
}

export interface MsdIndicationView {
  name: string;
  type: '급여' | '비급여';
  date: string; // 급여개시일 또는 MFDS 허가일 (없으면 '')
}

export interface MsdSummaryView {
  total: number;
  latestApplyDate: string | null;
  keytruda: {
    reimbursed: number;
    nonReimbursedApproved: number;
    indications: MsdIndicationView[];
  };
}

export async function fetchMsdSummary(): Promise<MsdSummaryView> {
  const raw = await api.get<RawMsdSummary>('/api/msd/summary');
  const items = raw.keytruda?.items ?? [];
  // 카드 노출 대상: 국내 맥락 — 급여 또는 MFDS 허가 적응증만 (해외 only 는 제외)
  const domestic = items.filter(i => i.is_reimbursed || i.mfds_approved);
  const indications: MsdIndicationView[] = domestic
    .map(i => ({
      name: i.title,
      type: (i.is_reimbursed ? '급여' : '비급여') as '급여' | '비급여',
      date: i.reimbursement_effective_date || i.mfds_date || '',
    }))
    .sort((a, b) => (a.type === b.type ? b.date.localeCompare(a.date) : a.type === '급여' ? -1 : 1));
  return {
    total: raw.reimbursed_product_count ?? 0,
    latestApplyDate: raw.latest_apply_date,
    keytruda: {
      reimbursed: raw.keytruda?.reimbursed_indications ?? 0,
      nonReimbursedApproved: items.filter(i => i.mfds_approved && !i.is_reimbursed).length,
      indications,
    },
  };
}

// ═════════════════════════════════════════════════════════════════════════════
// 2. MSD 급여 등재 품목 상세  (GET /api/msd/reimbursed-products)
// ═════════════════════════════════════════════════════════════════════════════

interface RawReimbursedProduct {
  insurance_code: string;
  product_name: string;
  brand_name: string;
  ingredient: string;
  dosage_form: string;
  dosage_strength: string;
  max_price: number;
  coverage_start: string;
}

export interface ReimbursedProductView {
  insuranceCode: string;
  name: string; // brand_name 우선
  ingredient: string;
  dosageForm: string;
  maxPrice: number;
}

export interface ReimbursedProductsView {
  latestApplyDate: string | null;
  count: number;
  items: ReimbursedProductView[];
}

export async function fetchReimbursedProducts(): Promise<ReimbursedProductsView> {
  const raw = await api.get<{ latest_apply_date: string | null; count: number; items: RawReimbursedProduct[] }>(
    '/api/msd/reimbursed-products'
  );
  return {
    latestApplyDate: raw.latest_apply_date,
    count: raw.count ?? 0,
    items: (raw.items ?? []).map(p => ({
      insuranceCode: p.insurance_code,
      name: p.brand_name || p.product_name,
      ingredient: p.ingredient || '',
      dosageForm: p.dosage_form || '',
      maxPrice: p.max_price ?? 0,
    })),
  };
}

// ═════════════════════════════════════════════════════════════════════════════
// 3. MSD Pipeline  (GET /api/msd/pipeline · POST /api/admin/msd/pipeline)
//    서버 스키마는 name/phase/indication/expected_year/status/note 뿐이므로
//    모달의 부가 필드(약제클래스·대상질환·국내 허가/급여일)는 note 에 JSON 으로 보존한다.
// ═════════════════════════════════════════════════════════════════════════════

interface RawPipelineItem {
  id: number;
  name: string;
  phase: string | null;
  indication: string | null;
  expected_year: number | null;
  status: 'current' | 'upcoming';
  note: string | null;
  created_at: string;
  updated_at: string;
}

/** readdy MsdSummaryCards 가 소비하는 형태 (mocks msdPipelineData 와 동일 + 서버 id) */
export interface PipelineItemView {
  id?: number;
  name: string;
  phase: string;
  indication: string;
  expectedYear: number;
  status: string;
  drugClass?: string;
  targetDisease?: string;
  domesticApprovalDate?: string;
  domesticReimbursementDate?: string;
  isCustom?: boolean;
}

interface PipelineNoteExtras {
  custom?: boolean;
  drugClass?: string;
  targetDisease?: string;
  domesticApprovalDate?: string;
  domesticReimbursementDate?: string;
}

function parseNoteExtras(note: string | null): PipelineNoteExtras {
  if (!note) return {};
  try {
    const parsed = JSON.parse(note);
    return parsed && typeof parsed === 'object' ? (parsed as PipelineNoteExtras) : {};
  } catch {
    return {};
  }
}

function adaptPipelineItem(r: RawPipelineItem): PipelineItemView {
  const extras = parseNoteExtras(r.note);
  return {
    id: r.id,
    name: r.name,
    phase: r.phase || '—',
    indication: r.indication || '—',
    expectedYear: r.expected_year ?? 0,
    status: r.status,
    drugClass: extras.drugClass || undefined,
    targetDisease: extras.targetDisease || undefined,
    domesticApprovalDate: extras.domesticApprovalDate || undefined,
    domesticReimbursementDate: extras.domesticReimbursementDate || undefined,
    isCustom: extras.custom === true,
  };
}

export async function fetchPipeline(): Promise<PipelineItemView[]> {
  const raw = await api.get<{ items: RawPipelineItem[] }>('/api/msd/pipeline');
  return (raw.items ?? []).map(adaptPipelineItem);
}

export interface PipelineCreateInput {
  name: string;
  phase: string;
  indication: string;
  expectedYear: number;
  status: 'current' | 'upcoming';
  drugClass?: string;
  targetDisease?: string;
  domesticApprovalDate?: string;
  domesticReimbursementDate?: string;
}

export async function createPipeline(input: PipelineCreateInput): Promise<PipelineItemView> {
  const extras: PipelineNoteExtras = { custom: true };
  if (input.drugClass) extras.drugClass = input.drugClass;
  if (input.targetDisease) extras.targetDisease = input.targetDisease;
  if (input.domesticApprovalDate) extras.domesticApprovalDate = input.domesticApprovalDate;
  if (input.domesticReimbursementDate) extras.domesticReimbursementDate = input.domesticReimbursementDate;
  const raw = await api.post<{ item: RawPipelineItem }>('/api/admin/msd/pipeline', {
    name: input.name,
    phase: input.phase || null,
    indication: input.indication || null,
    expected_year: input.expectedYear,
    status: input.status,
    note: JSON.stringify(extras),
  });
  return adaptPipelineItem(raw.item);
}

// ═════════════════════════════════════════════════════════════════════════════
// 4. 미디어 인텔리전스  (GET /api/home/media-intelligence · /api/home/brand-news)
//    Naver News 는 브랜드명만 제공 — 회사/계열/색상은 로컬 참조 매핑 (v2 검증 로직 port).
// ═════════════════════════════════════════════════════════════════════════════

interface RawBrandNews {
  title: string;
  url: string;
  source: string;
  date: string;
  description: string;
}

interface RawBrandTrafficEntry {
  brand: string;
  total_count: number;
  daily: Record<string, number>;
  sparkline: number[];
  latest_news: RawBrandNews[];
}

interface RawMediaIntelligence {
  updated_at: string;
  days: number;
  brands: RawBrandTrafficEntry[];
  error?: string;
}

export interface BrandNewsView {
  title: string;
  source: string;
  date: string;
  tag: string;
  url: string;
}

/** readdy KeywordCloud 가 소비하는 형태 (mocks brandTrafficData 와 동일) */
export interface BrandTrafficView {
  rank: number;
  brand: string;
  company: string;
  trafficIndex: number; // Naver 뉴스 건수 — 최근 7일 총계 (sparkline 합)
  change: number; // 최근 7일 vs 이전 7일 증감률(%)
  category: string;
  color: string;
  sparkline: number[]; // 최근 7일 일별 건수 (14일 창의 뒤 7일 slice)
  news: BrandNewsView[];
}

export interface MediaIntelligenceView {
  updatedAt: string | null;
  days: number | null;
  brands: BrandTrafficView[];
}

const BRAND_META: Record<string, { company: string; category: string; color: string }> = {
  '키트루다': { company: 'MSD', category: 'Anti-PD-1', color: '#00E5CC' },
  '렌비마': { company: 'MSD/Eisai', category: 'VEGF TKI', color: '#F59E0B' },
  '자누비아': { company: 'MSD', category: 'DPP-4', color: '#60A5FA' },
  '가다실': { company: 'MSD', category: 'HPV Vaccine', color: '#A78BFA' },
  '프로리아': { company: 'Amgen', category: 'RANKL', color: '#F472B6' },
  '옵디보': { company: 'BMS', category: 'Anti-PD-1', color: '#FB7185' },
  '타그리소': { company: 'AstraZeneca', category: 'EGFR TKI', color: '#34D399' },
  '임핀지': { company: 'AstraZeneca', category: 'Anti-PD-L1', color: '#22D3EE' },
  '테쎈트릭': { company: 'Roche', category: 'Anti-PD-L1', color: '#C084FC' },
  '레블리미드': { company: 'BMS', category: 'IMiD', color: '#FACC15' },
  '다잘렉스': { company: 'Janssen', category: 'Anti-CD38', color: '#FDBA74' },
  '린파자': { company: 'AstraZeneca', category: 'PARP', color: '#A3E635' },
};

/** 14일 일별 카운트 → 최근 7일 합 vs 이전 7일 합 증감률(%).
 *  change = round((sum(last7) - sum(prev7)) / sum(prev7) × 100).
 *  prev7 합이 0 이면 last7 존재 시 +100%, 둘 다 0 이면 0%. */
function computeChange(daily: number[]): number {
  if (!daily.length) return 0;
  const curr = daily.slice(-7).reduce((a, b) => a + b, 0);
  const prev = daily.slice(-14, -7).reduce((a, b) => a + b, 0);
  if (!prev) return curr ? 100 : 0;
  return Math.round(((curr - prev) / prev) * 100);
}

/** 제목/설명 기반 태그 분류 (rule-based) */
function inferTag(n: RawBrandNews): string {
  const text = `${n.title} ${n.description || ''}`;
  if (/급여|보험|등재|상한/.test(text)) return '급여';
  if (/약가|가격|인하|인상/.test(text)) return '약가';
  if (/승인|허가|적응증|FDA|EMA|식약처/.test(text)) return '허가';
  if (/임상|3상|2상|phase|trial/i.test(text)) return '임상';
  if (/매출|시장|점유|성장/.test(text)) return '시장';
  return '뉴스';
}

function adaptBrandNews(items: RawBrandNews[]): BrandNewsView[] {
  return (items ?? []).map(n => ({
    title: n.title,
    source: n.source || '',
    date: n.date || '',
    tag: inferTag(n),
    url: n.url,
  }));
}

export async function fetchMediaIntelligence(): Promise<MediaIntelligenceView> {
  const raw = await api.get<RawMediaIntelligence>('/api/home/media-intelligence');
  if (raw.error) throw new Error(raw.error);
  // 서버는 14일 창(daily/sparkline/total_count=14일 총계)을 준다.
  // 홈 노출 지표는 **최근 7일 총계** 로 통일 — trafficIndex/랭킹/스파크라인 모두 last7 기준,
  // change 는 last7 vs prev7 증감률. (total_count 는 14일 합이므로 사용하지 않음)
  const brands = (raw.brands ?? [])
    .map(b => {
      const full = b.sparkline ?? [];
      const last7 = full.slice(-7);
      return {
        brand: b.brand,
        trafficIndex: last7.reduce((a, c) => a + c, 0),
        change: computeChange(full),
        sparkline: last7,
        news: adaptBrandNews(b.latest_news),
      };
    })
    .sort((a, b) => b.trafficIndex - a.trafficIndex)
    .slice(0, 10)
    .map((b, i) => {
      const meta = BRAND_META[b.brand] ?? { company: '—', category: '—', color: '#8B9BB4' };
      return { rank: i + 1, company: meta.company, category: meta.category, color: meta.color, ...b };
    });
  return { updatedAt: raw.updated_at ?? null, days: raw.days ?? null, brands };
}

export async function fetchBrandNews(brand: string, limit = 10): Promise<BrandNewsView[]> {
  const q = new URLSearchParams({ brand, limit: String(limit) });
  const raw = await api.get<{ brand: string; count: number; items: RawBrandNews[] }>(
    `/api/home/brand-news?${q.toString()}`
  );
  return adaptBrandNews(raw.items);
}

// ═════════════════════════════════════════════════════════════════════════════
// 5. 정부 기관 키워드 요약  (GET /api/home/government-keyword-summary)
//    정책뉴스 아카이브(competitor_news kind='gov_policy')에서 LLM 이 키워드를 추출하고
//    각 키워드 → 실제 근거 기사를 백엔드가 직접 연결(newsByKeyword)·weight(freq×recency) 산정.
//    → 더 이상 사후 문자열 매칭 없음. 모든 키워드는 근거 기사가 보장됨('관련 뉴스 없음' 해소).
// ═════════════════════════════════════════════════════════════════════════════

interface RawGovNews {
  title: string;
  url: string;
  source: string;
  date: string;
}

interface RawGovSummary {
  updated_at: string;
  markdown: string;
  reviewers: string[];
  sources: RawGovNews[];
  keywords?: ({ text: string; weight?: number } | string)[];
  newsByKeyword?: Record<string, RawGovNews[]>;
  keyword_source?: string;
  archive_count?: number;
  error?: string;
}

export interface KeywordView {
  text: string;
  weight: number; // 시각 사이징 전용 (freq×recency 정규화) — 수치로 노출하지 않음
  color: string;
}

export interface GovNewsView {
  title: string;
  source: string;
  date: string;
  url: string;
}

export interface GovKeywordSummaryView {
  updatedAt: string | null;
  keywords: KeywordView[];
  newsByKeyword: Record<string, GovNewsView[]>;
  markdown: string;
  error?: string;
}

const KEYWORD_PALETTE = ['#00E5CC', '#F59E0B', '#00C9B1', '#8B9BB4', '#EF4444', '#6B7280'];

const mapGovNews = (arr?: RawGovNews[]): GovNewsView[] =>
  (arr ?? []).map(s => ({ title: s.title, source: s.source || '', date: s.date || '', url: s.url }));

export async function fetchGovKeywordSummary(): Promise<GovKeywordSummaryView> {
  const raw = await api.get<RawGovSummary>('/api/home/government-keyword-summary');
  // 백엔드가 {text, weight} 객체 배열을 보냄(구형 string[] 도 방어적 수용).
  const rawKw = (raw.keywords ?? []).map(k => (typeof k === 'string' ? { text: k } : k)).filter(k => k.text);
  const keywords: KeywordView[] = rawKw.map((k, idx) => ({
    text: k.text,
    weight: typeof k.weight === 'number' ? k.weight : 70,
    color: KEYWORD_PALETTE[idx % KEYWORD_PALETTE.length],
  }));
  // 키워드별 뉴스는 백엔드 제공 매핑을 그대로 사용 (근거 기사 보장).
  const newsByKeyword: Record<string, GovNewsView[]> = {};
  for (const k of rawKw) {
    newsByKeyword[k.text] = mapGovNews(raw.newsByKeyword?.[k.text]);
  }
  return {
    updatedAt: raw.updated_at ?? null,
    keywords,
    newsByKeyword,
    markdown: raw.markdown ?? '',
    error: raw.error,
  };
}

// ═════════════════════════════════════════════════════════════════════════════
// 5-b. 정부기관별 최근 7일 키워드 클라우드  (GET /api/home/gov-agency-clouds)
//     LLM 아님 — gov_policy 아카이브 기관별 title+description 순수 빈도.
//     홈 정부 위젯의 1차 소스 (5. government-keyword-summary 는 보존).
// ═════════════════════════════════════════════════════════════════════════════

interface RawGovAgencyKeyword {
  text: string;
  count: number;
}

interface RawGovAgencyCloud {
  agency: string;
  label: string;
  article_count?: number;
  keywords: RawGovAgencyKeyword[];
  newsByKeyword?: Record<string, RawGovNews[]>;
}

interface RawGovAgencyClouds {
  generated_at: string;
  window_days: number;
  agencies: RawGovAgencyCloud[];
  error?: string;
}

export interface GovAgencyKeywordView {
  text: string;
  count: number; // 최근 7일 실제 등장 빈도
  weight: number; // 시각 사이징 전용 — 기관 내 count 정규화 (50~100)
  color: string;
}

export interface GovAgencyCloudView {
  agency: string;
  label: string; // S4 영문태그는 한글(국회/환자단체/의료진)로 백엔드가 변환
  articleCount: number;
  keywords: GovAgencyKeywordView[];
  newsByKeyword: Record<string, GovNewsView[]>;
}

export interface GovAgencyCloudsView {
  generatedAt: string | null;
  windowDays: number;
  agencies: GovAgencyCloudView[];
  error?: string;
}

export async function fetchGovAgencyClouds(): Promise<GovAgencyCloudsView> {
  const raw = await api.get<RawGovAgencyClouds>('/api/home/gov-agency-clouds');
  const agencies: GovAgencyCloudView[] = (raw.agencies ?? []).map(a => {
    const kws = a.keywords ?? [];
    const maxCount = kws.length ? Math.max(...kws.map(k => k.count)) : 1;
    const newsByKeyword: Record<string, GovNewsView[]> = {};
    for (const k of kws) {
      newsByKeyword[k.text] = mapGovNews(a.newsByKeyword?.[k.text]);
    }
    return {
      agency: a.agency,
      label: a.label || a.agency,
      articleCount: a.article_count ?? 0,
      keywords: kws.map((k, idx) => ({
        text: k.text,
        count: k.count,
        weight: Math.round(50 + 50 * (k.count / (maxCount || 1))),
        color: KEYWORD_PALETTE[idx % KEYWORD_PALETTE.length],
      })),
      newsByKeyword,
    };
  });
  return {
    generatedAt: raw.generated_at ?? null,
    windowDays: raw.window_days ?? 7,
    agencies,
    error: raw.error,
  };
}

// ═════════════════════════════════════════════════════════════════════════════
// 6. 약가 변동 Top N  (GET /api/home/top-price-changes)
// ═════════════════════════════════════════════════════════════════════════════

interface RawTopPriceChangeItem {
  insurance_code: string;
  product_name: string;
  brand_name: string;
  ingredient: string;
  company: string;
  dosage_form: string;
  prev_price: number;
  curr_price: number;
  delta: number;
  delta_pct: number;
  variant_count: number;
  remark: string;
}

/** readdy 홈 테이블이 소비하는 형태 (mocks topPriceChangeDrugs 와 동일) */
export interface PriceChangeView {
  rank: number;
  productName: string;
  ingredient: string;
  company: string;
  prevPrice: number;
  currPrice: number;
  changeAmt: number;
  changeRate: number;
  reason: string; // 서버 remark ("외 N정" 등 비고) — 변동사유 미제공 시 ''
  date: string; // 최신 고시 적용일
}

export interface TopPriceChangesView {
  latestApplyDate: string | null;
  prevApplyDate: string | null;
  items: PriceChangeView[];
}

export async function fetchTopPriceChanges(limit = 10): Promise<TopPriceChangesView> {
  const raw = await api.get<{
    latest_apply_date: string | null;
    prev_apply_date: string | null;
    count: number;
    items: RawTopPriceChangeItem[];
  }>(`/api/home/top-price-changes?limit=${limit}`);
  const latest = raw.latest_apply_date ?? null;
  return {
    latestApplyDate: latest,
    prevApplyDate: raw.prev_apply_date ?? null,
    items: (raw.items ?? []).map((it, idx) => ({
      rank: idx + 1,
      productName: it.brand_name || it.product_name,
      ingredient: it.ingredient || '',
      company: it.company || '',
      prevPrice: it.prev_price ?? 0,
      currPrice: it.curr_price ?? 0,
      changeAmt: it.delta ?? 0,
      changeRate: it.delta_pct ?? 0,
      reason: it.remark || '',
      date: latest ?? '',
    })),
  };
}

/** "2026.04.01" → "2026년 4월" (파싱 실패 시 null) */
export function applyDateToYmLabel(applyDate: string | null): string | null {
  if (!applyDate) return null;
  const m = applyDate.match(/(\d{4})\D+(\d{1,2})/);
  if (!m) return null;
  return `${m[1]}년 ${Number(m[2])}월`;
}

// ═════════════════════════════════════════════════════════════════════════════
// 7. Market Share Mini  (GET /api/market-share/search → /api/market-share/atc4/<code>)
//    기본 anchor: keytruda → ATC4 (PD-1/PD-L1) 시장 점유율 (IQVIA values_lc 기준)
// ═════════════════════════════════════════════════════════════════════════════

interface RawMsSearchItem {
  product_name: string;
  molecule_desc: string;
  mfr_name: string;
  atc4_code: string;
  atc4_desc: string;
  pack_count: number;
  values_lc: number;
  dosage_units: number;
}

interface RawMsAtc4Product {
  product_name: string;
  molecule_desc: string;
  mfr_name: string;
  pack_count: number;
  values_lc: number;
  dosage_units: number;
  values_share_pct: number;
  units_share_pct: number;
}

export interface MarketShareSlice {
  name: string;
  value: number; // values_share_pct (%)
  color: string;
}

export interface MarketShareMiniView {
  atc4Code: string;
  atc4Desc: string;
  quarter: string | null;
  totalValuesLc: number;
  slices: MarketShareSlice[];
}

const SHARE_PALETTE = ['#00E5CC', '#7C3AED', '#F59E0B', '#EF4444', '#10B981', '#6B7280'];

export async function fetchMarketShareMini(query = 'keytruda'): Promise<MarketShareMiniView | null> {
  const search = await api.get<{ quarter: string | null; items: RawMsSearchItem[] }>(
    `/api/market-share/search?q=${encodeURIComponent(query)}`
  );
  const first = (search.items ?? [])[0];
  if (!first) return null;
  const detail = await api.get<{
    atc4_code: string;
    atc4_desc: string;
    quarter: string | null;
    totals: { values_lc: number; dosage_units: number };
    products: RawMsAtc4Product[];
  }>(`/api/market-share/atc4/${encodeURIComponent(first.atc4_code)}`);

  const products = detail.products ?? [];
  const top = products.slice(0, 5);
  const restPct = products.slice(5).reduce((s, p) => s + (p.values_share_pct ?? 0), 0);
  const slices: MarketShareSlice[] = top.map((p, i) => ({
    name: p.product_name,
    value: Math.round((p.values_share_pct ?? 0) * 10) / 10,
    color: SHARE_PALETTE[i % SHARE_PALETTE.length],
  }));
  if (restPct > 0.05) {
    slices.push({ name: '기타', value: Math.round(restPct * 10) / 10, color: SHARE_PALETTE[5] });
  }
  return {
    atc4Code: detail.atc4_code,
    atc4Desc: detail.atc4_desc,
    quarter: detail.quarter ?? null,
    totalValuesLc: detail.totals?.values_lc ?? 0,
    slices,
  };
}

/** "2025Q4" → "2025년 4분기" */
export function formatQuarterKr(quarter: string | null): string | null {
  if (!quarter) return null;
  const m = quarter.match(/(\d{4})Q(\d)/);
  if (!m) return quarter;
  return `${m[1]}년 ${m[2]}분기`;
}

/** 265,846,160,140 → "₩2,658억" / 1.2e12 → "₩1.2조" */
export function formatKrwCompact(v: number): string {
  if (!Number.isFinite(v) || v <= 0) return '—';
  if (v >= 1e12) return `₩${(v / 1e12).toFixed(1)}조`;
  if (v >= 1e8) return `₩${Math.round(v / 1e8).toLocaleString()}억`;
  return `₩${Math.round(v).toLocaleString()}`;
}
