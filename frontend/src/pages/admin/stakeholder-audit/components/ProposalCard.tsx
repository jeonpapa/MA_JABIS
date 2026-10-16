import { useEffect, useState } from 'react';
import {
  adminListConflictAcks,
  conflictsOf,
  OPEN_PROPOSAL_STATUSES,
  safeHttpUrl,
  type SVConflictAck,
  type SVConflictStatus,
  type SVProposal,
} from '@/api/stakeholder';
import { fmtKoDateTime } from '@/pages/stakeholder-signals/sketch/helpers';
import { LABELS } from './labels';

// 스케치 004 article.proposal 그대로 — 왼쪽: .headline(유형·상태 배지 + h2 요약) / .meta / .rationale / .change /
// .evidence / (검토 의견 .notice), 오른쪽 .actions(승인 · 반려).
// 스케치에 없는 하드 제약 2가지를 같은 시각 언어로 덧붙인다:
//   - 충돌·검토 메모(conflicts_status)를 충돌마다 한 행으로, 미확인 행에 "확인"(사유 모달) → 모두 확인돼야 승인 활성.
//     stale(확인 뒤 문구 변경) 행은 "재확인 필요" + 이전 확인 기록. 반려는 충돌과 무관하게 항상 가능.
//   - 서버 거부(EVIDENCE·CONFLICTS_UNACKNOWLEDGED 등)는 카드 맨 위에 서버 문구 그대로.

export interface CardError {
  action: 'approve' | 'reject' | 'ack';
  message: string;
  code?: string;
}

const ACTION_LABEL: Record<CardError['action'], string> = { approve: '승인', reject: '반려', ack: '충돌 확인' };

function changeValue(v: unknown): string {
  if (Array.isArray(v)) return v.join(', ');
  if (v && typeof v === 'object') return JSON.stringify(v);
  return v === null || v === undefined ? '' : String(v);
}

function Conflicts({
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
      .then(r => !cancelled && setAcks(r))
      .catch(() => !cancelled && setAcks([]));
    return () => {
      cancelled = true;
    };
  }, [pid, hasStale, proposal.updated_at]);

  if (!status.length) return null;
  const done = status.filter(c => c.acknowledged).length;
  return (
    <div className="conflicts">
      <b>
        충돌·검토 메모 {status.length}건 — 확인 {done}/{status.length}
        {open && done < status.length ? ' · 모두 확인해야 승인할 수 있습니다' : ''}
      </b>
      {status.map(c => {
        const state = c.acknowledged ? 'ack' : c.stale ? 'stale' : 'todo';
        const prev = acks?.find(a => a.conflict_index === c.index);
        return (
          <div key={c.index} className={`conflict${highlight.includes(c.index) ? ' hl' : ''}`} data-conflict-index={c.index}>
            <div>
              <span className="c-text">
                #{c.index} {c.text}
              </span>
              {state === 'ack' && (
                <span className="c-meta">
                  확인 {c.by || '—'} · {fmtKoDateTime(c.at)} · 사유: {c.reason || '—'}
                </span>
              )}
              {state === 'stale' && prev && (
                <span className="c-meta">
                  이전 확인 {prev.acknowledged_by || '—'} · {fmtKoDateTime(prev.acknowledged_at)} · 사유: {prev.reason || '—'}
                  {prev.conflict_text && (
                    <>
                      <br />
                      <s>당시 문구: {prev.conflict_text}</s>
                    </>
                  )}
                </span>
              )}
            </div>
            <div className="c-side">
              <span className={`badge status-${state}`}>{state === 'ack' ? '확인됨' : state === 'stale' ? '문구 변경 — 재확인 필요' : '미확인'}</span>
              {open && state !== 'ack' && (
                <button className="btn" onClick={() => onAck(c)} aria-label={`충돌 #${c.index} 확인`}>
                  확인
                </button>
              )}
            </div>
          </div>
        );
      })}
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
  const canApprove = open && unacknowledged.length === 0;
  const evidence = (p.evidence ?? []).filter(e => e && typeof e === 'object');

  return (
    <article className="proposal" aria-label={`감사 제안 ${p.proposal_id}`}>
      <div>
        {error && (
          <div className="server-error" role="alert">
            <b>
              서버가 {ACTION_LABEL[error.action]}을(를) 거부했습니다{error.code && <code>{error.code}</code>}
            </b>
            {error.message}
          </div>
        )}
        <div className="headline">
          <span className={`badge type-${p.proposal_type}`}>{LABELS[p.proposal_type] ?? p.proposal_type}</span>
          <span className={`badge status-${p.status}`}>{LABELS[p.status ?? ''] ?? p.status}</span>
          <h2>{p.summary}</h2>
        </div>
        <div className="meta">
          {p.priority} 우선순위 · {p.created_by} · {fmtKoDateTime(p.created_at)}
          {p.effective_from ? ` · ${p.effective_from}부터 적용` : ''}
        </div>
        <div className="rationale">{p.rationale}</div>
        <div className="change">
          {Object.entries(p.proposed_change ?? {}).map(([k, v]) => (
            <div key={k}>
              <b>{k}</b> · {changeValue(v)}
            </div>
          ))}
        </div>
        <div className="evidence">
          {evidence.length ? (
            evidence.map((e, i) => {
              const href = safeHttpUrl(e.url ?? null);
              const level = e.evidence_level ?? e.level;
              return (
                <div key={i}>
                  {href ? (
                    <a href={href} target="_blank" rel="noopener noreferrer">
                      {e.publisher || e.url}
                    </a>
                  ) : (
                    <span>{e.publisher || e.url}</span>
                  )}
                  <small>
                    {level} · {e.source_class}
                    {e.quoted_text ? ` · “${e.quoted_text}”` : ''}
                  </small>
                </div>
              );
            })
          ) : (
            <small>첨부 근거 없음</small>
          )}
        </div>
        <Conflicts proposal={p} status={conflicts} open={open} highlight={highlight} onAck={onAck} />
        {p.decision_reason && <div className="notice">검토 의견: {p.decision_reason}</div>}
      </div>
      <div className="actions">
        {open && (
          <>
            <button
              className="btn primary"
              onClick={onApprove}
              disabled={!canApprove}
              title={
                canApprove
                  ? undefined
                  : `미확인 충돌 ${unacknowledged.length}건(${unacknowledged.map(i => `#${i}`).join(', ')}) — 충돌마다 '확인' 후 승인할 수 있습니다.`
              }
            >
              승인
            </button>
            <button className="btn danger" onClick={onReject}>
              반려
            </button>
          </>
        )}
      </div>
    </article>
  );
}
