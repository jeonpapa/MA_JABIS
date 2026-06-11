import { api, getToken } from './client';

// ─────────────────────────────────────────────────────────────────────────────
// 국내약가 (HIRA) API — /api/domestic/price-changes 가 단일 권위 소스.
// /api/domestic/search 는 lean payload (이력·RSA·enrichment 없음) 라 테이블
// 컬럼(변동률/RSA/최종 변경일)을 채울 수 없어 검색 드라이버로 쓰지 않는다.
// v2(data/dashboard_v2/src/api/domestic.ts) 의 검증된 어댑터를 readdy 인터페이스로 포팅.
// 데이터 정직성: 없는 값은 null 유지 → UI 에서 '정보 없음'/'-' 표기. 임의 생성 금지.
// ─────────────────────────────────────────────────────────────────────────────

export interface DomesticPriceHistoryEntry {
  date: string;            // ISO YYYY-MM-DD
  price: number;
  type: string;            // 최초등재 | 약가인하 | 약가인상 | 유지
  reason: string;          // 최초등재→'신규 등재', 그 외 '미분석' (사유 분석 버튼으로 lazy 조회)
  changeRate: number | null;
}

export interface DomesticAnalogue {
  name: string;
  ingredient: string;
  price: number;
  dailyCost: number | null;
  company: string;
  // enrichment 연동용 (모달 표시는 위 5개만)
  normalizedName?: string;
  insuranceCode?: string;
  mergedCodes?: string[];
}

export interface DomesticProduct {
  id: string;                       // insurance_code
  productName: string;              // brand_name 우선
  fullProductName: string;          // HIRA 원문 제품명
  ingredient: string;               // 한글 주성분 (괄호 파싱)
  insuranceCode: string;
  mergedCodes: string[];
  company: string;
  normalizedName: string;
  category: string | null;          // 제형/함량 (dosage_form) — 치료영역 분류는 백엔드 미제공
  status: string;
  firstRegistDate: string;          // 최초 약가 등재일 (이력 첫 행)
  currentPrice: number;
  priceChangeCount: number;
  firstPrice: number;
  changeRateFromFirst: number;      // base_price_change_rate (마지막 행)
  change: number | null;            // 최근 변동률 delta_pct
  lastUpdated: string;
  priceHistory: DomesticPriceHistoryEntry[];
  sameIngredientCount: number;
  analogues: DomesticAnalogue[];

  // RSA — 표시가 ≠ 실제가 (net 비공개). is_rsa: 1=대상, 0=해당없음, null=미확인
  hasRSA: boolean;
  isRsa: 0 | 1 | null;
  rsaType: string | null;           // 한글 라벨 (환급형/총액제한형/…)
  rsaNote: string | null;

  // enrichment (drug_enrichment + MFDS permit JOIN) — 없으면 null → '정보 없음'
  // 출처 플래그: mfds_official=식약처 공공데이터 실측 / estimate=LLM 보강 추정 (백엔드 date_source 원칙)
  firstApprovalDate: string | null; // 식약처 최초 허가일 (MFDS 실측 우선)
  approvalDateSource: 'mfds_official' | 'estimate' | null;
  dosage: string | null;            // 용법용량 원문 (UI 에서 truncate)
  usageSource: 'mfds_official' | 'estimate' | null;
  dailyCost: number | null;
  monthlyCost: number | null;
  yearlyCost: number | null;
  enrichmentConfidence: string | null;

  // 백엔드 소스 없음 — UI 는 '정보 없음' 고정 (날조 금지)
  evalCommitteeDoc: null;
}

// ── raw 서버 응답 (api/server.py /api/domestic/price-changes) ────────────────
interface RawHistoryRow {
  date: string;                     // "2017.09.01"
  price: number;
  delta_pct: number | null;
  base_price_change_rate: number;
  change_type: string;              // 최초 | 인하 | 인상 | 유지
  price_change: number;
  is_first: boolean;
}

interface RawProduct {
  insurance_code: string;
  product_name: string;
  brand_name: string;
  ingredient: string;
  dosage_form: string | null;
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
  approval_date_source?: 'mfds_official' | 'estimate' | null;
  usage_text?: string | null;
  usage_text_source?: 'mfds_official' | 'estimate' | null;
  coverage_start?: string | null;
  daily_cost?: number | null;
  monthly_cost?: number | null;
  yearly_cost?: number | null;
  enrichment_confidence?: string | null;
  is_rsa?: 0 | 1 | null;
  rsa_type?: string | null;
  rsa_note?: string | null;
  mfds_permit?: { permit_date?: string | null } | null;
}

interface RawResponse {
  query: string;
  products: RawProduct[];
  dosage_forms: string[];
}

// ── 어댑터 헬퍼 ──────────────────────────────────────────────────────────────
function toIsoDate(dot: string | null | undefined): string | null {
  if (!dot) return null;
  return dot.replace(/\./g, '-');
}

function typeLabel(changeType: string, isFirst: boolean): string {
  if (isFirst || changeType === '최초') return '최초등재';
  if (changeType === '인상') return '약가인상';
  if (changeType === '인하') return '약가인하';
  return '유지';
}

/** 위험분담제 유형 코드 → 한글 라벨 */
const RSA_TYPE_LABEL: Record<string, string> = {
  refund: '환급형',
  expenditure_cap: '총액제한형',
  utilization: '사용량 제한형',
  conditional: '조건부 지속치료형',
  combined: '혼합형 (환급+총액제한)',
};

function rsaTypeLabel(code: string | null | undefined): string | null {
  if (!code) return null;
  return RSA_TYPE_LABEL[code] ?? code;
}

/** MFDS 용법용량 원문 정리 — 마크다운 헤더/HTML 엔티티 제거 (내용 변형 없음) */
function cleanUsageText(text: string | null | undefined): string | null {
  if (!text) return null;
  const cleaned = text
    .replace(/&#x2219;/g, '·')
    .replace(/&[a-z]+;/g, ' ')
    .replace(/^#{1,6}\s*/gm, '')
    .replace(/[ \t]+/g, ' ')
    .trim();
  return cleaned || null;
}

function mapHistory(rows: RawHistoryRow[]): DomesticPriceHistoryEntry[] {
  return rows.map(r => ({
    date: toIsoDate(r.date) ?? r.date,
    price: r.price,
    type: typeLabel(r.change_type, r.is_first),
    reason: r.is_first || r.change_type === '최초' ? '신규 등재' : '미분석',
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

  // 비교약제 후보 = 동일 성분 + 다른 제품(코드/정규화명). 성분 미상이면 검색결과 내 타제품.
  const analogues: DomesticAnalogue[] = allRaw
    .filter(p => {
      if (p.insurance_code === raw.insurance_code) return false;
      if (p.normalized_name && raw.normalized_name && p.normalized_name === raw.normalized_name) return false;
      if (raw.ingredient && p.ingredient) return p.ingredient === raw.ingredient;
      return true;
    })
    .slice(0, 100)
    .map(p => ({
      name: p.brand_name || p.product_name,
      ingredient: p.ingredient,
      price: p.current_price,
      dailyCost: p.daily_cost ?? null,
      company: p.company,
      normalizedName: p.normalized_name,
      insuranceCode: p.insurance_code,
      mergedCodes: p.merged_codes ?? [p.insurance_code],
    }));

  const permitDate = raw.mfds_permit?.permit_date ?? null;
  const approvalRaw = raw.approval_date ?? permitDate;

  return {
    id: raw.insurance_code,
    productName: raw.brand_name || raw.product_name,
    fullProductName: raw.product_name,
    ingredient: raw.ingredient,
    insuranceCode: raw.insurance_code,
    mergedCodes: raw.merged_codes ?? [raw.insurance_code],
    company: raw.company,
    normalizedName: raw.normalized_name ?? raw.brand_name ?? raw.product_name,
    category: raw.dosage_form ?? null,
    status: raw.status ?? 'active',
    firstRegistDate: toIsoDate(raw.first_date) ?? raw.first_date,
    currentPrice: lastPrice,
    priceChangeCount: Math.max(0, history.length - 1),
    firstPrice,
    changeRateFromFirst: Math.round(baseRate * 100) / 100,
    change: lastDelta,
    lastUpdated: history[history.length - 1]?.date ?? (toIsoDate(raw.first_date) ?? ''),
    priceHistory: history,
    sameIngredientCount: sameIng || 1,
    analogues,

    hasRSA: raw.is_rsa === 1,
    isRsa: raw.is_rsa ?? null,
    rsaType: rsaTypeLabel(raw.rsa_type),
    rsaNote: raw.rsa_note ?? null,

    firstApprovalDate: toIsoDate(approvalRaw),
    // 백엔드가 MFDS permit 발견 시 approval_date 자체를 실측값으로 override 하므로
    // permit 존재 = 표시값이 식약처 공식 (서버 플래그 우선, 구버전 응답 fallback 포함)
    approvalDateSource:
      raw.approval_date_source ?? (permitDate ? 'mfds_official' : raw.approval_date ? 'estimate' : null),
    dosage: cleanUsageText(raw.usage_text),
    usageSource: raw.usage_text_source ?? null,
    dailyCost: raw.daily_cost ?? null,
    monthlyCost: raw.monthly_cost ?? null,
    yearlyCost: raw.yearly_cost ?? null,
    enrichmentConfidence: raw.enrichment_confidence ?? null,

    evalCommitteeDoc: null,
  };
}

// ── fetchers ─────────────────────────────────────────────────────────────────

/** 검색 → 제품 목록 (가격 이력 + enrichment + RSA 포함). 테이블/상세 패널 공용. */
export async function searchDomesticPriceChanges(query: string): Promise<DomesticProduct[]> {
  const q = query.trim();
  if (!q) return [];
  const res = await api.get<RawResponse>(
    `/api/domestic/price-changes?q=${encodeURIComponent(q)}`,
  );
  return (res.products || []).map(p => mapProduct(p, res.products));
}

// ── on-demand enrichment (허가일·용법·일일투약비 — 캐시 우선, miss 시 LLM) ──
export interface EnrichmentResult {
  normalized_name: string;
  is_failure?: boolean;
  approval_date?: string | null;
  usage_text?: string;
  dose_schedule?: string | null;
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

/** 기준약제 + 선택 비교약제 일괄 enrich (최대 10건). 실패 시 호출측에서 무시. */
export async function enrichBulk(items: EnrichmentRequestItem[]): Promise<Record<string, EnrichmentResult>> {
  if (items.length === 0) return {};
  const res = await api.post<{ enrichments: Record<string, EnrichmentResult> }>(
    '/api/domestic/enrichment-bulk',
    { items: items.slice(0, 10) },
  );
  return res.enrichments || {};
}

// ── 변동사유 (4대 기전 분석 — lazy, 행 단위 클릭 시에만) ────────────────────
export interface ChangeReasonReference {
  title?: string;
  url: string;
  media?: string;
  journal?: string;
  weight?: number;
  published_at?: string;
  date_unknown?: boolean;
}

export interface ChangeReasonResult {
  mechanism: string;
  mechanism_label: string;
  reason: string;
  confidence: string;
  evidence_summary?: string;
  references?: ChangeReasonReference[];
  notes?: string;
  cached?: boolean;
  review?: { approved?: boolean; final_verdict?: string };
}

export async function fetchChangeReason(params: {
  drug: string;
  date: string;                // ISO 또는 dotted — 서버는 dotted 기대
  ingredient?: string;
  insuranceCode?: string;
  deltaPct?: number | null;
  refresh?: boolean;
}): Promise<ChangeReasonResult> {
  const q = new URLSearchParams({
    drug: params.drug,
    date: params.date.replace(/-/g, '.'),
  });
  if (params.ingredient) q.set('ingredient', params.ingredient);
  if (params.insuranceCode) q.set('insurance_code', params.insuranceCode);
  if (params.deltaPct != null) q.set('delta_pct', String(params.deltaPct));
  if (params.refresh) q.set('refresh', '1');
  return api.get<ChangeReasonResult>(`/api/domestic/change-reason?${q.toString()}`);
}

// ── 엑셀 다운로드 ────────────────────────────────────────────────────────────
export async function downloadDomesticExport(query: string, format: 'xlsx' | 'csv' = 'xlsx'): Promise<void> {
  const token = getToken();
  const res = await fetch(
    `/api/domestic/price-changes/export?q=${encodeURIComponent(query)}&format=${format}`,
    { headers: token ? { Authorization: `Bearer ${token}` } : undefined },
  );
  if (!res.ok) throw new Error(`다운로드 실패: HTTP ${res.status}`);
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
