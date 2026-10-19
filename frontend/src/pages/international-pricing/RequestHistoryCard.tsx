import { useState } from 'react';
import type { ReactNode, Ref } from 'react';
import type { PriceCountry, PriceRequest } from '@/api/priceRequests';
import { countState, countryLink, priceText, shortReason, STATE_LABEL, STATE_ORDER } from './requestSummary';
import type { CountryState } from './requestSummary';

const date = (value?: string | null) => value ? new Date(value).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul', year: 'numeric', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
const REQUEST_LABEL: Record<string, string> = {
  queued: '접수 대기', processing: '조사 중', awaiting_verification: '화면 검증 대기', verified: '검증됨 · 메일 대기',
  completed: '완료', partial_completed: '부분 완료', blocked: '진행 차단',
};
const NOTIFY_LABEL: Record<string, string> = { pending: '메일 대기', sending: '메일 발송 중', sent: '메일 발송됨', uncertain: '메일 발송 여부 확인 필요' };
const STATE_STYLE: Record<CountryState, { bg: string; fg: string; dot: string }> = {
  priced: { bg: 'var(--green-tint)', fg: 'var(--green)', dot: 'var(--green-solid)' },
  unconfirmed: { bg: 'var(--orange-tint)', fg: 'var(--orange)', dot: 'var(--orange-solid)' },
  failed: { bg: 'var(--red-tint)', fg: 'var(--red)', dot: 'var(--red-solid)' },
  pending: { bg: 'var(--fill)', fg: 'var(--label-2)', dot: 'var(--label-3)' },
};

function StatePill({ state, children }: { state: CountryState; children: ReactNode }) {
  const s = STATE_STYLE[state];
  return <span className="ui-pill whitespace-nowrap" style={{ background: s.bg, color: s.fg }}><span className="ui-dot" style={{ background: s.dot }} />{children}</span>;
}

interface Props {
  request: PriceRequest; catalog: PriceCountry[]; highlighted: boolean; defaultOpen: boolean;
  downloadBusy: boolean; downloadError: string; onDownload: () => void;
  articleRef?: Ref<HTMLElement>;
}

/** 요청 이력 카드 — 접힌 상태는 한 줄 요약(확인/미확정/실패 개수), 펼치면 나라별 상태·한 줄 사유·원문 버튼. */
export default function RequestHistoryCard({ request: r, catalog, highlighted, defaultOpen, downloadBusy, downloadError, onDownload, articleRef }: Props) {
  const [open, setOpen] = useState(defaultOpen);
  const [detail, setDetail] = useState<Record<string, boolean>>({});
  const byCode = new Map(catalog.map(c => [c.code, c]));
  const results = new Map(r.country_results.map(c => [c.country, c]));
  const counts = countState(r.country_results, r.countries);
  const total = r.countries.length;
  const canDownload = r.country_results.some(c => c.status !== 'pending');
  const listId = 'price-request-countries-' + r.id;
  const ordered = r.countries.map((code, i) => ({ code, i, state: (results.get(code)?.status || 'pending') as CountryState }))
    .sort((a, b) => STATE_ORDER[a.state] - STATE_ORDER[b.state] || a.i - b.i);

  return (
    <article ref={articleRef} id={'price-request-' + r.id}
      className={'rounded-2xl p-4 text-xs ' + (highlighted ? 'ring-2 ring-teal-500 scroll-mt-6' : '')} style={{ background: 'var(--surface-inset)' }}>
      {highlighted && <p className="font-semibold text-teal-600 mb-1">링크에서 지정한 요청</p>}
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <p className="text-[14px] font-semibold truncate" style={{ color: 'var(--label)' }}>{r.query}</p>
          <p className="mt-0.5" style={{ color: 'var(--label-2)' }}>
            {total}개국 요청 · {date(r.created_at)}{r.notification_status === 'sent' && r.notified_at ? ` · ${NOTIFY_LABEL.sent} ${date(r.notified_at)}` : ` · ${NOTIFY_LABEL[r.notification_status] || r.notification_status}`}
          </p>
        </div>
        <span className="ui-pill">{REQUEST_LABEL[r.status] || r.status}</span>
      </div>

      <div className="flex flex-wrap items-center gap-2 mt-3" aria-label="국가별 결과 요약">
        <StatePill state="priced">가격 확인됨 {counts.priced}</StatePill>
        {counts.unconfirmed > 0 && <StatePill state="unconfirmed">미확정 {counts.unconfirmed}</StatePill>}
        {counts.failed > 0 && <StatePill state="failed">수집 실패 {counts.failed}</StatePill>}
        {counts.pending > 0 && <StatePill state="pending">대기 {counts.pending}</StatePill>}
        <span style={{ color: 'var(--label-3)' }}>/ {total}개국</span>
      </div>

      <div className="flex flex-wrap items-center gap-2 mt-3">
        <button type="button" className="ui-button-secondary" aria-expanded={open} aria-controls={listId} onClick={() => setOpen(v => !v)}>
          {open ? '국가별 접기 ▴' : '국가별 보기 ▾'}
        </button>
        <button type="button" className="ui-button-secondary" disabled={!canDownload || downloadBusy} aria-busy={downloadBusy}
          title={canDownload ? '이 요청 당시 저장된 결과(미확정·실패 사유 포함)' : '한 국가라도 처리되면 받을 수 있습니다'}
          onClick={e => { e.stopPropagation(); onDownload(); }}>
          {downloadBusy ? '다운로드 중…' : 'Excel'}
        </button>
      </div>
      {downloadError && <p role="alert" className="text-red-500 mt-2">다운로드 실패: {downloadError}</p>}

      {open && (
        <div id={listId} className="mt-3 space-y-1.5">
          {(counts.unconfirmed + counts.failed) > 0 && <p style={{ color: 'var(--label-3)' }}>미확정·실패는 미허가나 가격 0을 뜻하지 않습니다.</p>}
          <ul className="divide-y rounded-xl overflow-hidden" style={{ background: 'var(--surface)', borderColor: 'var(--separator)' }}>
            {ordered.map(({ code, state }) => {
              const result = results.get(code);
              const source = r.country_sources?.[code];
              const link = countryLink(state, result, source, byCode.get(code));
              const summary = state === 'priced' ? (priceText(source) || '가격행 저장됨') : shortReason(state, result?.reason);
              const hasDetail = !!(result?.reason && result.reason.trim());
              return (
                <li key={code} className="px-3 py-2.5" style={{ borderColor: 'var(--separator)' }}>
                  <div className="flex items-center gap-3 flex-wrap">
                    <span className="w-[84px] shrink-0 font-semibold" style={{ color: 'var(--label)' }}>{byCode.get(code)?.label || code}</span>
                    <StatePill state={state}>{STATE_LABEL[state]}</StatePill>
                    <span className="flex-1 min-w-[160px]" style={{ color: 'var(--label-2)' }}>{summary}</span>
                    <span className="flex items-center gap-2 ml-auto">
                      {hasDetail && <button type="button" className="underline" style={{ color: 'var(--label-3)' }}
                        aria-expanded={!!detail[code]} onClick={() => setDetail(d => ({ ...d, [code]: !d[code] }))}>{detail[code] ? '접기' : '자세히'}</button>}
                      {link && <a href={link.url} target="_blank" rel="noopener noreferrer" className="ui-button-secondary !h-7 !px-3 !text-[12px]"
                        title={link.url}>{link.label} ↗</a>}
                    </span>
                  </div>
                  {detail[code] && hasDetail && <p className="mt-2 break-words leading-relaxed" style={{ color: 'var(--label-2)' }}>{result?.reason}{result?.updated_at ? ` (${date(result.updated_at)})` : ''}</p>}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </article>
  );
}
