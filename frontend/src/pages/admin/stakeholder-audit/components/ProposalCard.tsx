import { useEffect, useState } from 'react';
import {
  adminListConflictAcks,
  conflictsOf,
  evidenceGate,
  evidenceLevelOf,
  OPEN_PROPOSAL_STATUSES,
  PROPOSAL_EVENT_LABELS,
  PROPOSAL_STATUS_LABELS,
  PROPOSAL_TYPE_LABELS,
  safeHttpUrl,
  SOURCE_GROUP_LABELS,
  type SVConflictAck,
  type SVConflictStatus,
  type SVProposal,
} from '@/api/stakeholder';
import { badge, focusRing, tone, type Tone } from '@/pages/stakeholder-signals/theme/sv';
import { btnDanger, btnGhost, btnPrimary, fmtDateTime } from './uiHelpers';

// 감사 제안 카드 (스케치 004 + T2b "충돌별 확인 후 승인").
//   - 충돌은 conflicts_status 로 index 마다 한 행. 미확인 행에 "확인"(사유 모달) → POST …/conflicts/<index>/ack.
//   - 확인된 행은 확인자·시각·사유, stale 행은 "문구 변경 — 재확인 필요" + 이전 확인(…/conflict-acks).
//   - 승인 버튼은 conflicts_unacknowledged 가 비어 있을 때만 활성. 반려는 충돌과 무관하게 항상 가능.
//   - 서버 거부(EVIDENCE·CONFLICTS_UNACKNOWLEDGED 등)는 카드에 서버 문구 그대로 남긴다.

export interface CardError {
  action: 'approve' | 'reject' | 'ack';
  message: string;
  code?: string;
}

// 화이트 톤(스케치 004 .type-*/.status-*): 매체 확장=파랑 · 직책 변경=주황 · 신규 인물=초록.
const TYPE_TONE: Record<string, Tone> = {
  source_expansion: 'info',
  role_change: 'warn',
  new_person: 'ok',
};

// 검토 필요=주황 · 적용 예정=파랑(익일 효력 대기) · 적용 완료=초록 · 반려/적용 실패=빨강 · 그 외(대체됨)=회색
function statusTone(status?: string | null): string {
  const v = status || '';
  if (v === 'needs_review' || v === 'needs_evidence') return tone.warn;
  if (v === 'scheduled' || v === 'approved') return tone.info;
  if (v === 'applied') return tone.ok;
  if (v === 'rejected' || v === 'application_failed') return tone.bad;
  return tone.neutral;
}

const PRIORITY_LABELS: Record<string, string> = { urgent: '긴급', high: '높음', normal: '보통', low: '낮음' };

const ACTION_LABEL: Record<CardError['action'], string> = { approve: '승인', reject: '반려', ack: '충돌 확인' };

const CHANGE_KEY_LABELS: Record<string, string> = {
  name: '이름',
  organization: '기관',
  department: '부서',
  role: '직책',
  person_id: '인물 ID',
  domain: '도메인',
  source_group: '출처군',
  aliases: '별칭',
  topic_tags: '주제 태그',
};

function changeValue(key: string, v: unknown): string {
  if (v === null || v === undefined || v === '') return '—';
  if (Array.isArray(v)) return v.length ? v.map(x => String(x)).join(', ') : '—';
  if (typeof v === 'object') return JSON.stringify(v);
  if (key === 'source_group') return `${SOURCE_GROUP_LABELS[String(v)] ?? v} (${v})`;
  return String(v);
}

function ConflictRow({
  c,
  open,
  highlighted,
  prevAck,
  onAck,
}: {
  c: SVConflictStatus;
  open: boolean;
  highlighted: boolean;
  prevAck?: SVConflictAck;
  onAck: () => void;
}) {
  const state = c.acknowledged ? 'ok' : c.stale ? 'stale' : 'todo';
  const rowTone =
    state === 'ok'
      ? 'border-sv-ok-line bg-sv-surface'
      : state === 'stale'
        ? 'border-sv-warn-line bg-sv-warn-soft'
        : 'border-sv-bad-line bg-sv-bad-bg';
  return (
    <li
      data-conflict-index={c.index}
      className={`rounded-lg border px-3 py-2.5 ${rowTone} ${highlighted ? 'ring-2 ring-sv-bad-strong ring-offset-1' : ''}`}
    >
      <div className="flex flex-wrap items-start gap-2">
        <span className="font-mono text-[11px] text-sv-muted mt-0.5">#{c.index}</span>
        <p className="flex-1 min-w-[12rem] text-[13px] text-sv-ink-2 leading-relaxed whitespace-pre-wrap break-words">{c.text}</p>
        <div className="flex items-center gap-2 flex-shrink-0">
          {state === 'ok' && (
            <span className={`${badge} ${tone.ok}`}>
              <i className="ri-check-line"></i>확인됨
            </span>
          )}
          {state === 'stale' && (
            <span
              className={`${badge} ${tone.warn}`}
              title="확인한 뒤 충돌 문구가 바뀌어 이전 확인이 무효가 되었습니다."
            >
              <i className="ri-error-warning-line"></i>문구 변경 — 재확인 필요
            </span>
          )}
          {state === 'todo' && (
            <span className={`${badge} ${tone.badStrong}`}>
              <i className="ri-question-line"></i>미확인
            </span>
          )}
          {open && state !== 'ok' && (
            <button
              className={`rounded-md bg-sv-surface px-2.5 py-1 text-[12px] font-bold text-sv-ink shadow-sv-ring hover:bg-sv-surface-3 cursor-pointer ${focusRing}`}
              onClick={onAck}
              aria-label={`충돌 #${c.index} 확인`}
            >
              확인
            </button>
          )}
        </div>
      </div>
      {state === 'ok' && (
        <p className="mt-1.5 pl-6 text-[12px] text-sv-muted">
          <span className="text-sv-ink font-semibold">{c.by || '—'}</span> · {fmtDateTime(c.at)} · 사유: {c.reason || '—'}
        </p>
      )}
      {state === 'stale' && prevAck && (
        <div className="mt-1.5 pl-6 text-[12px] text-sv-muted space-y-0.5">
          <p>
            이전 확인: <span className="text-sv-ink font-semibold">{prevAck.acknowledged_by || '—'}</span> ·{' '}
            {fmtDateTime(prevAck.acknowledged_at)} · 사유: {prevAck.reason || '—'}
          </p>
          {prevAck.conflict_text && (
            <p className="line-through opacity-70 whitespace-pre-wrap break-words">당시 문구: {prevAck.conflict_text}</p>
          )}
        </div>
      )}
    </li>
  );
}

function ConflictsBox({
  proposal,
  status,
  open,
  highlight,
  onAck,
}: {
  proposal: SVProposal;
  status: SVConflictStatus[];
  open: boolean;
  highlight: number[];
  onAck: (c: SVConflictStatus) => void;
}) {
  const [acks, setAcks] = useState<SVConflictAck[] | null>(null);
  const hasStale = status.some(c => c.stale);
  const pid = proposal.proposal_id;
  useEffect(() => {
    if (!hasStale) return;
    let cancelled = false;
    adminListConflictAcks(pid)
      .then(r => {
        if (!cancelled) setAcks(r);
      })
      .catch(() => {
        if (!cancelled) setAcks([]);
      });
    return () => {
      cancelled = true;
    };
  }, [pid, hasStale, proposal.updated_at]);

  if (!status.length) return null;
  const done = status.filter(c => c.acknowledged).length;
  return (
    <div className="mt-3.5 rounded-lg border border-sv-warn-line bg-sv-warn-soft p-3">
      <p className="mb-2 flex items-center gap-1.5 text-[13px] font-extrabold text-sv-warn-soft-fg">
        <i className="ri-alert-line"></i>충돌·검토 메모 {status.length}건 — 확인 {done}/{status.length}
      </p>
      <ul className="space-y-1.5">
        {status.map(c => (
          <ConflictRow
            key={c.index}
            c={c}
            open={open}
            highlighted={highlight.includes(c.index)}
            prevAck={acks?.find(a => a.conflict_index === c.index)}
            onAck={() => onAck(c)}
          />
        ))}
      </ul>
    </div>
  );
}

export default function ProposalCard({
  proposal: p,
  error,
  highlight,
  onApprove,
  onReject,
  onAck,
}: {
  proposal: SVProposal;
  error?: CardError | null;
  highlight: number[];
  onApprove: () => void;
  onReject: () => void;
  onAck: (c: SVConflictStatus) => void;
}) {
  const open = (OPEN_PROPOSAL_STATUSES as readonly string[]).includes(p.status ?? '');
  const { status: conflicts, unacknowledged } = conflictsOf(p);
  const gate = evidenceGate(p);
  const canApprove = open && unacknowledged.length === 0;
  const change = Object.entries(p.proposed_change ?? {});
  const evidence = (p.evidence ?? []).filter(e => e && typeof e === 'object');
  const events = p.events ?? [];

  return (
    <article className="p-4 sm:p-5" aria-label={`감사 제안 ${p.proposal_id}`}>
      <div className="flex flex-col lg:flex-row lg:items-start gap-4 lg:gap-5">
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`${badge} !text-[11px] !px-[9px] !py-[5px] ${tone[TYPE_TONE[p.proposal_type] ?? 'neutral']}`}>
              {PROPOSAL_TYPE_LABELS[p.proposal_type] ?? p.proposal_type}
            </span>
            <span className={`${badge} !text-[11px] !px-[9px] !py-[5px] ${statusTone(p.status)}`}>
              {PROPOSAL_STATUS_LABELS[p.status ?? ''] ?? p.status ?? '—'}
            </span>
            <span className="font-mono text-[11px] text-sv-muted">{p.proposal_id}</span>
          </div>
          <h3 className="mt-2 text-[17px] font-extrabold text-sv-ink leading-snug sv-ko">{p.summary || '(요약 없음)'}</h3>
          <p className="mt-2 text-[12px] leading-[1.6] text-sv-muted">
            우선순위 {PRIORITY_LABELS[p.priority ?? ''] ?? p.priority ?? '—'} · {p.created_by || '—'} · {fmtDateTime(p.created_at)}
            {p.audit_run_id ? ` · 감사 런 ${p.audit_run_id}` : ''}
            {p.effective_from ? ` · ${p.effective_from}부터 적용` : ''}
          </p>
        </div>

        {open && (
          <div className="flex flex-col items-stretch lg:items-end gap-1.5 lg:w-64 flex-shrink-0">
            <div className="grid grid-cols-2 gap-2 w-full">
              <button
                className={btnPrimary}
                onClick={onApprove}
                disabled={!canApprove}
                aria-describedby={`approve-why-${p.proposal_id}`}
              >
                <i className="ri-check-double-line"></i>승인
              </button>
              <button className={btnDanger} onClick={onReject}>
                <i className="ri-close-circle-line"></i>반려
              </button>
            </div>
            <p id={`approve-why-${p.proposal_id}`} className="text-[11px] leading-snug lg:text-right w-full">
              {unacknowledged.length > 0 ? (
                <span className="text-sv-warn-fg font-semibold">
                  미확인 충돌 {unacknowledged.length}건({unacknowledged.map(i => `#${i}`).join(', ')}) — 충돌마다 &lsquo;확인&rsquo; 후 승인할 수 있습니다.
                </span>
              ) : (
                <span className="text-sv-muted">승인 시 익일부터 적용됩니다. 반려는 언제든 가능합니다.</span>
              )}
            </p>
          </div>
        )}
      </div>

      {error && (
        <div role="alert" className="mt-3 rounded-lg border-2 border-sv-bad-strong bg-sv-bad-bg px-3.5 py-3">
          <p className="text-[13px] font-extrabold text-sv-bad-fg">
            <i className="ri-forbid-line mr-1"></i>서버가 {ACTION_LABEL[error.action]}을(를) 거부했습니다
            {error.code ? (
              <span className="ml-1.5 rounded bg-sv-bad-solid px-1.5 py-px font-mono text-[11px] font-bold">{error.code}</span>
            ) : null}
          </p>
          <p className="mt-1 text-[13px] text-sv-ink-2 whitespace-pre-wrap">{error.message}</p>
        </div>
      )}

      {p.rationale && (
        <p className="mt-2.5 text-[14px] text-sv-text leading-[1.7] whitespace-pre-wrap break-words">{p.rationale}</p>
      )}

      {change.length > 0 && (
        <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 rounded-lg bg-sv-surface-3 px-3 py-3 text-[12px] leading-[1.65]">
          {change.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-sv-muted">{CHANGE_KEY_LABELS[k] ?? k}</dt>
              <dd className="text-sv-ink font-semibold break-words">{changeValue(k, v)}</dd>
            </div>
          ))}
        </dl>
      )}

      <div className="mt-3">
        <div className="flex flex-wrap items-center gap-2 mb-1.5">
          <p className="text-[12px] font-bold text-sv-text">근거 {evidence.length}건</p>
          <span
            className={`${badge} ${gate.met ? tone.ok : tone.warn}`}
            title="참고 표시 — 승인 가능 여부의 최종 판정은 서버가 합니다."
          >
            {gate.summary} · 기준 {gate.required} {gate.met ? '충족' : '미충족(승인 시 서버가 거부할 수 있음)'}
          </span>
        </div>
        {evidence.length === 0 ? (
          <p className="text-[12px] text-sv-muted">첨부 근거 없음</p>
        ) : (
          <ul className="space-y-1.5">
            {evidence.map((e, i) => {
              const href = safeHttpUrl(e.url ?? null);
              return (
                <li key={i} className="rounded-lg border border-sv-line px-3 py-2 text-[12px]">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded bg-sv-dark px-1.5 py-0.5 font-mono text-[10px] font-bold text-sv-on-dark">{evidenceLevelOf(e)}</span>
                    <span className="text-sv-muted">{e.source_class || '—'}</span>
                    {href ? (
                      <a
                        href={href}
                        target="_blank"
                        rel="noopener noreferrer"
                        className={`font-semibold text-sv-accent-ink hover:underline break-all ${focusRing}`}
                        title={href}
                      >
                        <i className="ri-external-link-line mr-0.5"></i>
                        {e.publisher || href}
                      </a>
                    ) : (
                      <span className="text-sv-ink">{e.publisher || e.url || '—'}</span>
                    )}
                    {e.publication_date && <span className="text-sv-muted">게시 {String(e.publication_date).slice(0, 10)}</span>}
                  </div>
                  {e.quoted_text && <p className="mt-1 text-sv-ink-2 break-words">&ldquo;{e.quoted_text}&rdquo;</p>}
                  {e.locator && <p className="mt-0.5 font-mono text-[11px] text-sv-muted break-all">{e.locator}</p>}
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <ConflictsBox proposal={p} status={conflicts} open={open} highlight={highlight} onAck={onAck} />

      {p.decision_reason && (
        <p className={`mt-3 rounded-lg px-3.5 py-3 text-[12px] leading-relaxed ${tone.notice}`}>
          검토 의견{p.reviewer ? ` (${p.reviewer}, ${fmtDateTime(p.reviewed_at)})` : ''}: {p.decision_reason}
        </p>
      )}

      {events.length > 0 && (
        <details className="mt-3 group">
          <summary className={`${btnGhost} !min-h-0 !py-1 !px-2.5 !text-[12px] list-none w-fit`}>
            <i className="ri-history-line"></i>처리 이력 {events.length}
          </summary>
          <ol className="mt-2 space-y-1 border-l-2 border-sv-line pl-3">
            {events.map((ev, i) => (
              <li key={ev.id ?? i} className="text-[12px] text-sv-muted">
                <span className="text-sv-ink font-semibold">{PROPOSAL_EVENT_LABELS[ev.event ?? ''] ?? ev.event}</span> ·{' '}
                {ev.actor || '—'} · {fmtDateTime(ev.created_at)}
                {ev.detail ? <span className="block text-sv-muted break-words">{ev.detail}</span> : null}
              </li>
            ))}
          </ol>
        </details>
      )}
    </article>
  );
}
