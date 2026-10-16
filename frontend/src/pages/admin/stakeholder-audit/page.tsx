import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { fetchMe } from '@/utils/authUsers';
import {
  adminAckConflict,
  adminApprove,
  adminListProposals,
  adminReject,
  adminSourceDomains,
  notifyProposalsChanged,
  OPEN_PROPOSAL_STATUSES,
  SVApiError,
  svErrorMessage,
  unacknowledgedFromError,
  type SVConflictStatus,
  type SVProposal,
} from '@/api/stakeholder';
import { fmtKoDateTime } from '@/pages/stakeholder-signals/sketch/helpers';
import ProposalCard, { type CardError } from './components/ProposalCard';
import { ReasonModal, Toast } from './components/ui';
import { useToast } from './components/uiHelpers';
import { LABELS } from './components/labels';
import '@/pages/stakeholder-signals/sketch/base.css';
import '@/pages/stakeholder-signals/sketch/common.css';
import '@/pages/stakeholder-signals/sketch/s004.css';
import '@/pages/stakeholder-signals/sketch/s004.additions.css';

// 모니터링 감사 = 스케치 004(main 영역) 그대로 — /admin/stakeholder-audit (admin 전용)
//   경로 + 안내(승인 변경은 익일 07:00 KST부터) / 제목·부제 + 지표 2(검토 필요·승인 출처) / 원칙 3 /
//   필터 칩(검토 필요·전체·매체 확장·직책 변경·신규 인물) + 최근 감사 변경 / 제안 목록 / 토스트.
//   감사 에이전트(외부)는 제안만 넣고, 승인·반려·충돌 확인은 admin 이 사유와 함께(스케치 prompt → 사유 모달).
//   승인 → scheduled(익일 효력) → 매일 sync 잡의 apply_scheduled 가 applied 로 반영.

type Filter = 'open' | 'all' | 'source_expansion' | 'role_change' | 'new_person';
const FILTERS: [Filter, string][] = [
  ['open', '검토 필요'],
  ['all', '전체'],
  ['source_expansion', '매체 확장'],
  ['role_change', '직책 변경'],
  ['new_person', '신규 인물'],
];
const isOpen = (s?: string) => (OPEN_PROPOSAL_STATUSES as readonly string[]).includes(s ?? '');

type ModalState =
  | { kind: 'approve' | 'reject'; proposal: SVProposal }
  | { kind: 'ack'; proposal: SVProposal; conflict: SVConflictStatus }
  | null;

export default function AdminStakeholderAuditPage() {
  const navigate = useNavigate();
  const [authChecked, setAuthChecked] = useState(false);
  const [proposals, setProposals] = useState<SVProposal[] | null>(null);
  const [pending, setPending] = useState<number | null>(null);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [domainCount, setDomainCount] = useState<number | null>(null);
  const [filter, setFilter] = useState<Filter>('open');
  const [cardErrors, setCardErrors] = useState<Record<string, CardError>>({});
  const [highlights, setHighlights] = useState<Record<string, number[]>>({});
  const [modal, setModal] = useState<ModalState>(null);
  const [toast, showToast] = useToast();

  useEffect(() => {
    (async () => {
      try {
        const me = await fetchMe();
        if (!me || me.role !== 'admin') {
          navigate('/', { replace: true });
          return;
        }
        setAuthChecked(true);
      } catch {
        navigate('/login', { replace: true });
      }
    })();
  }, [navigate]);

  const load = useCallback(async () => {
    try {
      const [p, s] = await Promise.all([adminListProposals(), adminSourceDomains()]);
      setProposals(p.proposals);
      setPending(typeof p.pending_count === 'number' ? p.pending_count : p.proposals.filter(x => isOpen(x.status)).length);
      setUpdatedAt(p.updated_at ?? null);
      setDomainCount(typeof s.effective_count === 'number' ? s.effective_count : s.items.length);
      setError(null);
    } catch (e) {
      setError(svErrorMessage(e));
    }
  }, []);

  useEffect(() => {
    if (authChecked) void load();
  }, [authChecked, load]);

  const visible = useMemo(
    () => (proposals ?? []).filter(p => filter === 'all' || (filter === 'open' && isOpen(p.status)) || p.proposal_type === filter),
    [proposals, filter],
  );

  const setCardError = (id: string, err: CardError | null) =>
    setCardErrors(m => {
      const n = { ...m };
      if (err) n[id] = err;
      else delete n[id];
      return n;
    });

  /** 서버 거부 — BAD_REASON 은 모달에 그대로(throw), 나머지는 모달을 닫고 카드에 서버 문구를 남긴다. */
  const handleError = (p: SVProposal, action: CardError['action'], e: unknown) => {
    if (e instanceof SVApiError && e.code === 'BAD_REASON') throw e;
    const message = svErrorMessage(e);
    setCardError(p.proposal_id, { action, message, code: e instanceof SVApiError ? e.code : undefined });
    const unack = unacknowledgedFromError(e);
    if (unack.length) {
      setHighlights(h => ({ ...h, [p.proposal_id]: unack }));
      window.setTimeout(() => {
        document
          .querySelector(`[aria-label="감사 제안 ${CSS.escape(p.proposal_id)}"] [data-conflict-index="${unack[0]}"]`)
          ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }, 50);
    }
    setModal(null);
    showToast(message, 'error');
    void load();
  };

  const decide = async (p: SVProposal, kind: 'approve' | 'reject', reason: string) => {
    try {
      await (kind === 'approve' ? adminApprove(p.proposal_id, reason) : adminReject(p.proposal_id, reason));
      setCardError(p.proposal_id, null);
      setHighlights(h => ({ ...h, [p.proposal_id]: [] }));
      setModal(null);
      await load();
      showToast(`${kind === 'approve' ? '승인' : '반려'}했습니다.${kind === 'approve' ? ' 익일부터 반영됩니다.' : ''}`);
      notifyProposalsChanged();
    } catch (e) {
      handleError(p, kind, e);
    }
  };

  const ack = async (p: SVProposal, c: SVConflictStatus, reason: string) => {
    try {
      const r = await adminAckConflict(p.proposal_id, c.index, reason);
      setCardError(p.proposal_id, null);
      setHighlights(h => ({ ...h, [p.proposal_id]: (h[p.proposal_id] ?? []).filter(i => i !== c.index) }));
      setModal(null);
      await load();
      const left = r?.conflicts_unacknowledged?.length;
      showToast(
        typeof left === 'number' && left > 0 ? `충돌 #${c.index} 확인을 기록했습니다. 남은 미확인 ${left}건.` : `충돌 #${c.index} 확인을 기록했습니다. 이제 승인할 수 있습니다.`,
      );
    } catch (e) {
      handleError(p, 'ack', e);
    }
  };

  if (!authChecked) return <div className="sxbase sx4" />;

  return (
    <div className="sxbase sx4">
      <div className="main">
        <div className="top">
          <div className="crumb">약가·건강보험 재정 / 모니터링 감사</div>
          <div className="notice">승인 변경은 익일 07:00 KST부터 적용</div>
        </div>
        <section className="hero">
          <div>
            <h1>모니터링 감사·확장</h1>
            <p>
              전문 감사 에이전트가 검색 공백, 현재 직책 변동, 신규 관련 발언자를 독립적으로 점검합니다.
              <br />
              자동 발견은 정본을 바꾸지 않으며, 근거와 승인 게이트를 통과한 변경만 적용됩니다.
            </p>
          </div>
          <div className="stats">
            <div className="stat">
              검토 필요<b>{pending === null ? '—' : `${pending}건`}</b>
            </div>
            <div className="stat">
              승인 출처<b>{domainCount === null ? '—' : `${domainCount}개`}</b>
            </div>
          </div>
        </section>
        <section className="principles">
          <div className="principle">
            <b>1. 매체 확장</b>
            <p>기존 목록 밖 독립 발언·신규 공식 자료실·반복 단독취재 출처를 점검합니다.</p>
          </div>
          <div className="principle">
            <b>2. 현재 직책</b>
            <p>공식 E3 이상 근거로 확인하며 행사 당시 직책과 현재 직책을 분리합니다.</p>
          </div>
          <div className="principle">
            <b>3. 신규 인물</b>
            <p>관련 직접발언자를 포착하되 승인 후에도 후보 단계로 등록합니다.</p>
          </div>
        </section>
        <section className="toolbar">
          <div className="chips" role="group" aria-label="감사 제안 필터">
            {FILTERS.map(([k, label]) => (
              <button key={k} className={`chip${filter === k ? ' active' : ''}`} onClick={() => setFilter(k)}>
                {label}
              </button>
            ))}
          </div>
          <span className="meta">{proposals ? `최근 감사 변경 ${fmtKoDateTime(updatedAt)}` : '불러오는 중'}</span>
        </section>
        <section className="panel" aria-live="polite">
          {error ? (
            <div className="empty">
              <b>감사 데이터를 불러오지 못했습니다.</b>
              <p>{error}</p>
            </div>
          ) : !proposals ? (
            <div className="empty">감사 제안을 불러오는 중입니다.</div>
          ) : visible.length === 0 ? (
            <div className="empty">
              <b>현재 조건에 해당하는 감사 제안이 없습니다.</b>
              <p>감사 에이전트는 매일 07:40 KST에 점검합니다.</p>
            </div>
          ) : (
            visible.map(p => (
              <ProposalCard
                key={p.proposal_id}
                proposal={p}
                error={cardErrors[p.proposal_id] ?? null}
                highlight={highlights[p.proposal_id] ?? []}
                onApprove={() => setModal({ kind: 'approve', proposal: p })}
                onReject={() => setModal({ kind: 'reject', proposal: p })}
                onAck={c => setModal({ kind: 'ack', proposal: p, conflict: c })}
              />
            ))
          )}
        </section>
      </div>

      {modal && modal.kind !== 'ack' && (
        <ReasonModal
          title={`${LABELS[modal.proposal.proposal_type] ?? '감사'} 제안 ${modal.kind === 'approve' ? '승인' : '반려'}`}
          tone={modal.kind === 'approve' ? 'primary' : 'danger'}
          confirmLabel={modal.kind === 'approve' ? '승인' : '반려'}
          description={
            <>
              <p>
                <b>{modal.proposal.summary}</b>
              </p>
              <p>
                {modal.kind === 'approve'
                  ? '승인하면 익일(KST)부터 적용 예정으로 바뀌고, 서버가 근거 기준을 다시 검증합니다.'
                  : '반려는 충돌 확인 여부와 무관하게 가능하며 되돌릴 수 없습니다.'}
              </p>
            </>
          }
          onClose={() => setModal(null)}
          onSubmit={reason => decide(modal.proposal, modal.kind as 'approve' | 'reject', reason)}
        />
      )}
      {modal && modal.kind === 'ack' && (
        <ReasonModal
          title={`충돌 #${modal.conflict.index} 확인`}
          confirmLabel="확인 기록"
          description={
            <>
              <p className="conflicts" style={{ marginTop: 0 }}>
                {modal.conflict.text}
              </p>
              <p>이 충돌을 검토했고 승인을 막을 사유가 아님을 기록합니다. 확인은 지금 문구에 대해서만 유효하며, 문구가 바뀌면 다시 확인해야 합니다.</p>
            </>
          }
          onClose={() => setModal(null)}
          onSubmit={reason => ack(modal.proposal, modal.conflict, reason)}
        />
      )}
      <Toast toast={toast} />
    </div>
  );
}
