/** 해외약가 요청 이력 카드용 요약 — 긴 워커 사유를 '확인됨/안됨 + 한 줄 사유 + 원문 링크' 로 줄인다.
 *  원 사유 전문은 카드의 '자세히' 에서 그대로 보여 준다(요약이 근거를 대체하지 않는다). */
import type { CountryProgress, CountrySource, PriceCountry } from '@/api/priceRequests';

export type CountryState = 'priced' | 'unconfirmed' | 'failed' | 'pending';

export const STATE_LABEL: Record<CountryState, string> = {
  priced: '가격 확인됨', unconfirmed: '가격 미확정', failed: '수집 실패', pending: '조사 대기',
};

/** 사유 분류 — 앞에서부터 처음 맞는 규칙. 순서가 의미를 가진다(접근 차단 > 시간 초과 > 검색 0건 > 가격 칸 비어 있음 …).
 *  '검색 0건' 이 '가격 칸' 보다 앞이다: '브랜드 0건, 성분만 확인' 을 '등재됐으나 가격 비공개' 로 요약하면 원문에 없는 등재 사실을 단정한다(2026-10-22 칠레·슬로베니아). */
const RULES: Array<[RegExp, string]> = [
  [/제안가|비확정|확정가격 .*미완료/i, '확정 가격 미확인'],
  [/로그인|login|KULLANICI/i, '로그인 필요한 출처'],
  [/Forbidden|HTTP 403|접근 차단/i, '사이트 접근 차단'],
  [/Ocorreu um erro|generic\.jsf|서비스 오류|사이트 자체 오류|error page/i, '사이트 오류'],
  [/timeout|타임아웃|시간 초과|timed out/i, '사이트 응답 없음'],
  [/렌더링|빈 페이지|빈 결과|로딩 실패|검색폼 접근 불가|조회 불가/i, '사이트 화면 로딩 실패'],
  [/최대가격.*미설정|nemá stanovenou/i, '가격 미설정 (등재 전·비급여)'],
  [/Tillhandahålls ej|공급 안 함/i, '현지 공급 안 함 · 가격 없음'],
  [/과거자료만|최신 자료 없음/i, '최신 공개자료 없음'],
  [/0건|검색 0|No hay resultados|No se han obtenido|ei leitud|共0|geen gegevens|일치 없음|No encontramos|לא נמצאו/i, '공개 가격목록에서 찾을 수 없음'],
  // 등재 여부를 단정하지 않는다: '가격 칸이 비어 있다' 는 사실만 말한다. 원문이 '비공개 확정 아님' 이라고 하면 이 규칙을 건너뛴다.
  [/^(?![\s\S]*(?:비공개\s*확정\s*아님|비공개\s*아님))[\s\S]*(?:가격 필드 없음|가격 미표시|가격란|가격칸|가격 칸|가격공란|금액 필드 없음|금액 없음)/i, '공식 화면에 가격 표시 없음'],
  [/가격 필드 없음|가격 미표시|가격란|가격칸|가격 칸|가격공란|금액 필드 없음|금액 없음/i, '가격 칸이 비어 있음 (비공개 확정 아님)'],
  [/확보하지 못함|확보 못함|미확보/i, '공개 가격 미확보'],
];

export function shortReason(state: CountryState, reason: string | null | undefined): string {
  const text = (reason || '').trim();
  if (state === 'pending') return '아직 조사 전입니다';
  if (state === 'priced') return '';
  for (const [re, label] of RULES) if (re.test(text)) return label;
  if (!text) return state === 'failed' ? '수집 실패 (사유 미기록)' : '가격 미확정 (사유 미기록)';
  const first = text.replace(/^\d{4}-\d{2}-\d{2}[^:]*:\s*/, '').split(/(?<=[.。])\s|;\s/)[0];
  return first.length > 60 ? first.slice(0, 58) + '…' : first;
}

/** 사유 안의 첫 번째 공개 URL(워커가 실제로 확인한 페이지). 끝 문장부호는 뗀다. */
export function firstUrl(reason: string | null | undefined): string | null {
  const m = (reason || '').match(/https?:\/\/[^\s"'<>()\]]+/);
  if (!m) return null;
  return m[0].replace(/[.,;:)\]]+$/, '');
}

const TIER: Record<string, string> = {
  ex_factory: '공장도가', pharmacy_purchase: '약국구입가', pharmacy_retail: '약국판매가', reimbursement: '급여가', other: '기타 가격',
};
const VAT: Record<string, string> = { included: 'VAT 포함', excluded: 'VAT 제외', unknown: 'VAT 미확인' };

export function priceText(source: CountrySource | undefined): string {
  const p = source?.price;
  if (!p || p.local_price == null) return '';
  const amount = Number(p.local_price).toLocaleString('en-US', { maximumFractionDigits: 2 });
  const tier = TIER[p.price_tier || ''] || '';
  const vat = VAT[p.vat_status || ''] || '';
  return [`${p.currency || ''} ${amount}`.trim(), [tier, vat].filter(Boolean).join(', ')].filter(Boolean).join(' · ');
}

export interface CountryLink { url: string; label: string }

/** 버튼 링크 우선순위: 가격행 출처 → 사유 속 확인 URL → 국가 공식 출처(카탈로그). */
export function countryLink(state: CountryState, result: CountryProgress | undefined, source: CountrySource | undefined,
                            catalog: PriceCountry | undefined): CountryLink | null {
  if (state === 'priced' && source?.source_url) return { url: source.source_url, label: '출처 열기' };
  const fromReason = firstUrl(result?.reason);
  if (fromReason) return { url: fromReason, label: '확인한 사이트' };
  if (catalog?.source_url) return { url: catalog.source_url, label: '공식 사이트' };
  return null;
}

export function countState(results: CountryProgress[], countries: string[]) {
  const by = new Map(results.map(r => [r.country, r.status as CountryState]));
  const counts: Record<CountryState, number> = { priced: 0, unconfirmed: 0, failed: 0, pending: 0 };
  for (const code of countries) counts[by.get(code) || 'pending'] += 1;
  return counts;
}

/** 나라 정렬: 확인됨 → 미확정 → 실패 → 대기, 같은 상태 안에서는 요청 순서. */
export const STATE_ORDER: Record<CountryState, number> = { priced: 0, unconfirmed: 1, failed: 2, pending: 3 };
