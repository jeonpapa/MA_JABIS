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
import { btnDanger, btnGhost, btnPrimary } from './ui';
import { fmtDateTime } from './uiHelpers';

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

const TYPE_TONE: Record<string, string> = {
  source_expansion: 'bg-[#3B82F6]/10 text-[#60A5FA]',
  role_change: 'bg-[#F59E0B]/10 text-[#F59E0B]',
  new_person: 'bg-[#22C55E]/10 text-[#4ADE80]',
};

function statusTone(status?: string | null): string {
  const v = status || '';
  if (v === 'needs_review' || v === 'needs_evidence') return 'bg-[#F59E0B]/10 text-[#F59E0B]';
  if (v === 'scheduled' || v === 'approved') return 'bg-[#00E5CC]/10 text-[#00E5CC]';
  if (v === 'applied') return 'bg-[#22C55E]/10 text-[#4ADE80]';
  if (v === 'rejected' || v === 'application_failed') return 'bg-red-500/10 text-red-400';
  return 'bg-[#1E2530] text-[#8B9BB4]';
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
  const tone =
    state === 'ok'
      ? 'border-[#00E5CC]/20 bg-[#00E5CC]/5'
      : state === 'stale'
        ? 'border-[#F59E0B]/40 bg-[#F59E0B]/5'
        : 'border-red-500/30 bg-red-500/5';
  return (
    <li
      data-conflict-index={c.index}
      className={`rounded-lg border px-3 py-2.5 ${tone} ${highlighted ? 'ring-2 ring-red-500/70' : ''}`}
    >
      <div className="flex flex-wrap items-start gap-2">
        <span className="font-mono text-[10px] text-[#8B9BB4] mt-0.5">#{c.index}</span>
        <p className="flex-1 min-w-[12rem] text-xs text-[#E2E8F0] leading-relaxed whitespace-pre-wrap break-words">{c.text}</p>
        <div className="flex items-center gap-2 flex-shrink-0">
          {state === 'ok' && (
            <span className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold bg-[#00E5CC]/10 text-[#00E5CC]">
              <i className="ri-check-line"></i>확인됨
            </span>
          )}
          {state === 'stale' && (
            <span
              className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold bg-[#F59E0B]/15 text-[#F59E0B]"
              title="확인한 뒤 충돌 문구가 바뀌어 이전 확인이 무효가 되었습니다."
            >
              <i className="ri-error-warning-line"></i>문구 변경 — 재확인 필요
            </span>
          )}
          {state === 'todo' && (
            <span className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold bg-red-500/10 text-red-400">
              <i className="ri-question-line"></i>미확인
            </span>
          )}
          {open && state !== 'ok' && (
            <button
              className="rounded-md border border-[#00E5CC]/40 px-2.5 py-1 text-[11px] font-semibold text-[#00E5CC] hover:bg-[#00E5CC]/10 cursor-pointer"
              onClick={onAck}
              aria-label={`충돌 #${c.index} 확인`}
            >
              확인
            </button>
          )}
        </div>
      </div>
      {state === 'ok' && (
        <p className="mt-1.5 pl-6 text-[11px] text-[#8B9BB4]">
          <span className="text-[#C9D1DD]">{c.by || '—'}</span> · {fmtDateTime(c.at)} · 사유: {c.reason || '—'}
        </p>
      )}
      {state === 'stale' && prevAck && (
        <div className="mt-1.5 pl-6 text-[11px] text-[#8B9BB4] space-y-0.5">
          <p>
            이전 확인: <span className="text-[#C9D1DD]">{prevAck.acknowledged_by || '—'}</span> ·{' '}
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
    <div className="mt-3 rounded-xl border border-[#F59E0B]/30 bg-[#F59E0B]/5 p-3">
      <p className="mb-2 flex items-center gap-1.5 text-xs font-bold text-[#F59E0B]">
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
    <article className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5" aria-label={`감사 제안 ${p.proposal_id}`}>
      <div className="flex flex-col lg:flex-row lg:items-start gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${TYPE_TONE[p.proposal_type] ?? 'bg-[#1E2530] text-[#8B9BB4]'}`}>
              {PROPOSAL_TYPE_LABELS[p.proposal_type] ?? p.proposal_type}
            </span>
            <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${statusTone(p.status)}`}>
              {PROPOSAL_STATUS_LABELS[p.status ?? ''] ?? p.status ?? '—'}
            </span>
            <span className="font-mono text-[10px] text-[#4A5568]">{p.proposal_id}</span>
          </div>
          <h3 className="mt-2 text-sm font-bold text-white leading-snug">{p.summary || '(요약 없음)'}</h3>
          <p className="mt-1 text-[11px] text-[#8B9BB4]">
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
            <p id={`approve-why-${p.proposal_id}`} className="text-[10px] leading-snug text-right w-full">
              {unacknowledged.length > 0 ? (
                <span className="text-[#F59E0B]">
                  미확인 충돌 {unacknowledged.length}건({unacknowledged.map(i => `#${i}`).join(', ')}) — 충돌마다 &lsquo;확인&rsquo; 후 승인할 수 있습니다.
                </span>
              ) : (
                <span className="text-[#4A5568]">승인 시 익일부터 적용됩니다. 반려는 언제든 가능합니다.</span>
              )}
            </p>
          </div>
        )}
      </div>

      {error && (
        <div role="alert" className="mt-3 rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2.5">
          <p className="text-xs font-bold text-red-400">
            <i className="ri-forbid-line mr-1"></i>서버가 {ACTION_LABEL[error.action]}을(를) 거부했습니다
            {error.code ? <span className="ml-1.5 font-mono font-normal text-red-400/80">{error.code}</span> : null}
          </p>
          <p className="mt-1 text-xs text-red-200 whitespace-pre-wrap">{error.message}</p>
        </div>
      )}

      {p.rationale && (
        <p className="mt-3 text-xs text-[#C9D1DD] leading-relaxed whitespace-pre-wrap break-words">{p.rationale}</p>
      )}

      {change.length > 0 && (
        <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 rounded-xl bg-[#0D1117] border border-[#1E2530] px-3 py-2.5 text-xs">
          {change.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-[#8B9BB4]">{CHANGE_KEY_LABELS[k] ?? k}</dt>
              <dd className="text-white break-words">{changeValue(k, v)}</dd>
            </div>
          ))}
        </dl>
      )}

      <div className="mt-3">
        <div className="flex flex-wrap items-center gap-2 mb-1.5">
          <p className="text-[11px] font-semibold text-[#8B9BB4]">근거 {evidence.length}건</p>
          <span
            className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
              gate.met ? 'bg-[#00E5CC]/10 text-[#00E5CC]' : 'bg-[#F59E0B]/10 text-[#F59E0B]'
            }`}
            title="참고 표시 — 승인 가능 여부의 최종 판정은 서버가 합니다."
          >
            {gate.summary} · 기준 {gate.required} {gate.met ? '충족' : '미충족(승인 시 서버가 거부할 수 있음)'}
          </span>
        </div>
        {evidence.length === 0 ? (
          <p className="text-[11px] text-[#4A5568]">첨부 근거 없음</p>
        ) : (
          <ul className="space-y-1.5">
            {evidence.map((e, i) => {
              const href = safeHttpUrl(e.url ?? null);
              return (
                <li key={i} className="rounded-lg border border-[#1E2530] px-3 py-2 text-[11px]">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded bg-[#1E2530] px-1.5 py-0.5 font-mono text-[10px] text-white">{evidenceLevelOf(e)}</span>
                    <span className="text-[#8B9BB4]">{e.source_class || '—'}</span>
                    {href ? (
                      <a
                        href={href}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-[#00E5CC] hover:underline break-all"
                        title={href}
                      >
                        <i className="ri-external-link-line mr-0.5"></i>
                        {e.publisher || href}
                      </a>
                    ) : (
                      <span className="text-white">{e.publisher || e.url || '—'}</span>
                    )}
                    {e.publication_date && <span className="text-[#4A5568]">게시 {String(e.publication_date).slice(0, 10)}</span>}
                  </div>
                  {e.quoted_text && <p className="mt-1 text-[#C9D1DD] break-words">&ldquo;{e.quoted_text}&rdquo;</p>}
                  {e.locator && <p className="mt-0.5 font-mono text-[10px] text-[#4A5568] break-all">{e.locator}</p>}
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <ConflictsBox proposal={p} status={conflicts} open={open} highlight={highlight} onAck={onAck} />

      {p.decision_reason && (
        <p className="mt-3 rounded-lg bg-[#3B82F6]/10 px-3 py-2 text-xs text-[#93C5FD]">
          검토 의견{p.reviewer ? ` (${p.reviewer}, ${fmtDateTime(p.reviewed_at)})` : ''}: {p.decision_reason}
        </p>
      )}

      {events.length > 0 && (
        <details className="mt-3 group">
          <summary className={`${btnGhost} !py-1 !px-2 !text-[11px] list-none w-fit`}>
            <i className="ri-history-line"></i>처리 이력 {events.length}
          </summary>
          <ol className="mt-2 space-y-1 border-l border-[#1E2530] pl-3">
            {events.map((ev, i) => (
              <li key={ev.id ?? i} className="text-[11px] text-[#8B9BB4]">
                <span className="text-[#C9D1DD] font-semibold">{PROPOSAL_EVENT_LABELS[ev.event ?? ''] ?? ev.event}</span> ·{' '}
                {ev.actor || '—'} · {fmtDateTime(ev.created_at)}
                {ev.detail ? <span className="block text-[#4A5568] break-words">{ev.detail}</span> : null}
              </li>
            ))}
          </ol>
        </details>
      )}
    </article>
  );
}
