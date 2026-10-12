import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { fetchMe } from '@/utils/authUsers';
import {
  adminAckConflict,
  adminApprove,
  adminIntegrity,
  adminListProposals,
  adminReject,
  adminSourceDomains,
  adminSync,
  fetchOverview,
  fetchRuns,
  notifyProposalsChanged,
  OPEN_PROPOSAL_STATUSES,
  PROPOSAL_TYPE_LABELS,
  SVApiError,
  svErrorMessage,
  unacknowledgedFromError,
  type SVConflictStatus,
  type SVIntegrityReport,
  type SVOverview,
  type SVProposal,
  type SVRun,
} from '@/api/stakeholder';
import ProposalCard, { type CardError } from './components/ProposalCard';
import RunsPanel, { RunStatus } from './components/RunsPanel';
import IntegrityPanel from './components/IntegrityPanel';
import { ReasonModal, StatCard, Toast } from './components/ui';
import { btnGhost, btnPrimary, chipCls, fmtDateTime, useToast } from './components/uiHelpers';
import { boxCard, errorBox, focusRing, panel, tone } from '@/pages/stakeholder-signals/theme/sv';
import { SvHero, SvMain, SvPage, SvTopBar } from '@/pages/stakeholder-signals/theme/SvPage';

// 모니터링 감사 (스케치 004, 화이트 톤 .sv-light) — /admin/stakeholder-audit
//   감사 에이전트(외부)는 제안만 넣고, 승인·반려·충돌 확인은 여기서 admin 이 사유와 함께 한다.
//   승인 → scheduled(익일 효력) → 매일 sync 잡의 apply_scheduled 가 applied 로 반영.
//   서버 오류는 {code, message, details} — 한국어 message 를 그대로 보여준다.

type Tab = 'proposals' | 'runs' | 'integrity';
type StatusFilter = 'open' | 'scheduled' | 'closed' | 'all';
type ModalState =
  | { kind: 'approve' | 'reject'; proposal: SVProposal }
  | { kind: 'ack'; proposal: SVProposal; conflict: SVConflictStatus }
  | null;

interface Loadable<T> {
  data: T;
  loading: boolean;
  error: string | null;
}

const STATUS_FILTERS: { key: StatusFilter; label: string; match: (s: string) => boolean }[] = [
  { key: 'open', label: '검토 필요', match: s => (OPEN_PROPOSAL_STATUSES as readonly string[]).includes(s) },
  { key: 'scheduled', label: '적용 예정', match: s => s === 'scheduled' || s === 'approved' },
  {
    key: 'closed',
    label: '처리 완료',
    match: s => ['applied', 'rejected', 'superseded', 'application_failed'].includes(s),
  },
  { key: 'all', label: '전체', match: () => true },
];

const TYPE_FILTERS = ['all', 'source_expansion', 'role_change', 'new_person'] as const;

const PRINCIPLES = [
  {
    title: '1. 매체 확장',
    body: '기존 목록 밖 독립 발언·신규 공식 자료실·반복 단독취재 출처를 점검합니다. 승인에는 E2 이상(공식 출처군은 공식 E3 이상) 근거와 도메인이 일치하는 근거 URL 이 필요합니다.',
  },
  {
    title: '2. 현재 직책',
    body: '공식 E3 이상 근거로만 확인하며, 행사 당시 직책과 현재 직책을 분리합니다. 승인된 직책은 익일 반영과 함께 이력으로 보존되고 인물이 검증 상태로 바뀝니다.',
  },
  {
    title: '3. 신규 인물',
    body: '관련 직접발언자를 포착하되 승인 후에도 후보(candidate) 단계로 등록합니다. 검증 전환은 공식 직책 근거가 승인될 때만 일어납니다.',
  },
];

function summarizeSync(r: Record<string, unknown>): string {
  const parts: string[] = [];
  ['status', 'result', 'message', 'summary'].forEach(k => {
    const v = r[k];
    if (typeof v === 'string' && v) parts.push(v);
  });
  if (parts.length) return parts.join(' · ').slice(0, 200);
  const s = JSON.stringify(r);
  return s.length > 200 ? `${s.slice(0, 200)}…` : s;
}

export default function AdminStakeholderAuditPage() {
  const navigate = useNavigate();
  const [authChecked, setAuthChecked] = useState(false);
  const [tab, setTab] = useState<Tab>('proposals');

  const [proposals, setProposals] = useState<Loadable<SVProposal[]>>({ data: [], loading: true, error: null });
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [domainCount, setDomainCount] = useState<number | null>(null);
  const [overview, setOverview] = useState<SVOverview | null>(null);
  const [monRuns, setMonRuns] = useState<Loadable<SVRun[]>>({ data: [], loading: true, error: null });
  const [audRuns, setAudRuns] = useState<Loadable<SVRun[]>>({ data: [], loading: true, error: null });
  const [integrity, setIntegrity] = useState<Loadable<SVIntegrityReport | null>>({
    data: null,
    loading: true,
    error: null,
  });

  const [statusFilter, setStatusFilter] = useState<StatusFilter>('open');
  const [typeFilter, setTypeFilter] = useState<(typeof TYPE_FILTERS)[number]>('all');
  const [cardErrors, setCardErrors] = useState<Record<string, CardError>>({});
  const [highlights, setHighlights] = useState<Record<string, number[]>>({});
  const [modal, setModal] = useState<ModalState>(null);
  const [syncing, setSyncing] = useState(false);
  const [toast, showToast] = useToast();

  // admin 셀프 가드 (service-requests 패턴) — 비관리자는 홈으로
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

  const loadProposals = useCallback(async () => {
    setProposals(s => ({ ...s, loading: true, error: null }));
    try {
      const r = await adminListProposals();
      setProposals({ data: r.proposals, loading: false, error: null });
      setUpdatedAt(r.updated_at ?? null);
    } catch (e) {
      setProposals({ data: [], loading: false, error: svErrorMessage(e) });
    }
  }, []);

  const loadAll = useCallback(async () => {
    void loadProposals();
    adminSourceDomains()
      .then(r => setDomainCount(typeof r.effective_count === 'number' ? r.effective_count : r.items.length))
      .catch(() => setDomainCount(null));
    fetchOverview()
      .then(setOverview)
      .catch(() => setOverview(null));
    setMonRuns(s => ({ ...s, loading: true }));
    fetchRuns('monitoring', 10)
      .then(d => setMonRuns({ data: d, loading: false, error: null }))
      .catch(e => setMonRuns({ data: [], loading: false, error: svErrorMessage(e) }));
    setAudRuns(s => ({ ...s, loading: true }));
    fetchRuns('audit', 10)
      .then(d => setAudRuns({ data: d, loading: false, error: null }))
      .catch(e => setAudRuns({ data: [], loading: false, error: svErrorMessage(e) }));
    setIntegrity(s => ({ ...s, loading: true }));
    adminIntegrity()
      .then(d => setIntegrity({ data: d, loading: false, error: null }))
      .catch(e => setIntegrity({ data: null, loading: false, error: svErrorMessage(e) }));
  }, [loadProposals]);

  useEffect(() => {
    if (authChecked) void loadAll();
  }, [authChecked, loadAll]);

  const all = proposals.data;
  const openCount = useMemo(
    () => all.filter(p => (OPEN_PROPOSAL_STATUSES as readonly string[]).includes(p.status ?? '')).length,
    [all],
  );
  const statusMatch = STATUS_FILTERS.find(f => f.key === statusFilter)!.match;
  const byStatus = useMemo(() => all.filter(p => statusMatch(p.status ?? '')), [all, statusMatch]);
  const visible = useMemo(
    () => byStatus.filter(p => typeFilter === 'all' || p.proposal_type === typeFilter),
    [byStatus, typeFilter],
  );

  const replaceProposal = (next: SVProposal) => {
    setProposals(s => ({
      ...s,
      data: s.data.map(p => (p.proposal_id === next.proposal_id ? { ...p, ...next, events: next.events ?? p.events } : p)),
    }));
  };

  const setCardError = (id: string, err: CardError | null) =>
    setCardErrors(m => {
      const n = { ...m };
      if (err) n[id] = err;
      else delete n[id];
      return n;
    });

  /** 서버 거부 처리. BAD_REASON 은 모달에 그대로 두고(throw), 나머지는 모달을 닫고 카드에 서버 문구를 남긴다. */
  const handleDecisionError = (p: SVProposal, action: CardError['action'], e: unknown) => {
    if (e instanceof SVApiError && e.code === 'BAD_REASON') throw e;
    const message = svErrorMessage(e);
    const code = e instanceof SVApiError ? e.code : undefined;
    setCardError(p.proposal_id, { action, message, code });
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
    void loadProposals(); // 409(상태·경합) 뒤에도 서버 상태가 바뀌었을 수 있다
  };

  const doDecision = async (p: SVProposal, kind: 'approve' | 'reject', reason: string) => {
    try {
      const r = kind === 'approve' ? await adminApprove(p.proposal_id, reason) : await adminReject(p.proposal_id, reason);
      setCardError(p.proposal_id, null);
      setHighlights(h => ({ ...h, [p.proposal_id]: [] }));
      if (r && r.proposal_id) replaceProposal(r);
      setModal(null);
      showToast(
        kind === 'approve'
          ? `승인했습니다. ${r?.effective_from ? `${r.effective_from}부터` : '익일부터'} 적용됩니다.`
          : '반려했습니다.',
      );
      notifyProposalsChanged();
      void loadProposals();
    } catch (e) {
      handleDecisionError(p, kind, e);
    }
  };

  const doAck = async (p: SVProposal, c: SVConflictStatus, reason: string) => {
    try {
      const r = await adminAckConflict(p.proposal_id, c.index, reason);
      setCardError(p.proposal_id, null);
      setHighlights(h => ({ ...h, [p.proposal_id]: (h[p.proposal_id] ?? []).filter(i => i !== c.index) }));
      if (r && r.proposal_id) replaceProposal(r);
      else void loadProposals();
      setModal(null);
      const left = r?.conflicts_unacknowledged?.length;
      showToast(
        typeof left === 'number' && left > 0
          ? `충돌 #${c.index} 확인을 기록했습니다. 남은 미확인 ${left}건.`
          : `충돌 #${c.index} 확인을 기록했습니다.${typeof left === 'number' ? ' 이제 승인할 수 있습니다.' : ''}`,
      );
    } catch (e) {
      handleDecisionError(p, 'ack', e);
    }
  };

  const runSync = async () => {
    setSyncing(true);
    try {
      const r = await adminSync();
      showToast(`sync 완료 — ${summarizeSync(r ?? {})}`);
      notifyProposalsChanged();
      void loadAll();
    } catch (e) {
      showToast(
        `sync 실패 — ${svErrorMessage(e)}${e instanceof SVApiError && e.code ? ` (${e.code})` : ''}`,
        'error',
      );
    } finally {
      setSyncing(false);
    }
  };

  if (!authChecked) {
    return <SvPage>{null}</SvPage>;
  }

  const latestMon = monRuns.data[0];
  const latestAud = audRuns.data[0];
  const lastSync = overview?.integrity?.last_sync_at ?? integrity.data?.last_sync_at ?? null;

  return (
    <SvPage>
      <SvMain>
        <SvTopBar crumb="약가·건강보험 재정 / 모니터링 감사">
          <span className={`rounded-lg px-3.5 py-2.5 text-[12px] ${tone.notice}`}>
            <i className="ri-calendar-schedule-line mr-1"></i>승인 변경은 익일 07:00 KST부터 적용
          </span>
          <button className={btnGhost} onClick={() => void loadAll()}>
            <i className="ri-refresh-line"></i>새로고침
          </button>
          <button className={btnPrimary} onClick={runSync} disabled={syncing}>
            <i className={syncing ? 'ri-loader-4-line animate-spin' : 'ri-git-repository-line'}></i>
            {syncing ? 'sync 중…' : '지금 sync'}
          </button>
        </SvTopBar>

        <SvHero
          alignTop
          title="모니터링 감사·확장"
          sub={
            <>
              전문 감사 에이전트가 검색 공백, 현재 직책 변동, 신규 관련 발언자를 독립적으로 점검합니다.
              <br className="hidden sm:block" />
              자동 발견은 정본을 바꾸지 않으며, 근거와 승인 게이트를 통과한 변경만 적용됩니다.
            </>
          }
          aside={
            <div className="grid grid-cols-2 gap-2.5 lg:w-[420px]">
              <StatCard
                label="검토 필요"
                value={proposals.loading && !all.length ? '—' : `${openCount}건`}
                tone={openCount > 0 ? 'text-sv-warn-fg' : undefined}
                sub={`최근 감사 변경 ${fmtDateTime(updatedAt)}`}
              />
              <StatCard label="승인 출처" value={domainCount === null ? '—' : `${domainCount}개`} sub="현재 유효 출처 도메인" />
              <StatCard
                label="최근 모니터링 런"
                value={latestMon?.run_date ?? '—'}
                sub={
                  latestMon ? (
                    <span className="inline-flex flex-wrap items-center gap-1">
                      <RunStatus status={latestMon.run_status} />
                      <span>saturation</span>
                      <RunStatus status={latestMon.saturation_status} />
                    </span>
                  ) : (
                    '런 없음'
                  )
                }
              />
              <StatCard
                label="최근 sync"
                value={<span className="text-[15px]">{fmtDateTime(lastSync)}</span>}
                sub={
                  latestAud ? (
                    <span className="inline-flex flex-wrap items-center gap-1">
                      최근 감사 {latestAud.run_date} <RunStatus status={latestAud.run_status} />
                    </span>
                  ) : (
                    '감사 런 없음'
                  )
                }
              />
            </div>
          }
        />

        <section className="grid grid-cols-1 md:grid-cols-3 gap-2.5 mb-[18px]">
          {PRINCIPLES.map(p => (
            <div key={p.title} className={`${boxCard} p-4`}>
              <b className="block text-[14px] font-extrabold text-sv-ink mb-1.5">{p.title}</b>
              <p className="text-[12px] leading-[1.6] text-sv-text sv-ko">{p.body}</p>
            </div>
          ))}
        </section>

        <nav className="flex items-center gap-5 border-b border-sv-line mb-4 overflow-x-auto" aria-label="감사 보기 전환">
          {(
            [
              ['proposals', '감사 제안', 'ri-file-list-3-line'],
              ['runs', '런 이력', 'ri-history-line'],
              ['integrity', '출처 무결성', 'ri-fingerprint-line'],
            ] as [Tab, string, string][]
          ).map(([k, label, icon]) => (
            <button
              key={k}
              onClick={() => setTab(k)}
              aria-pressed={tab === k}
              className={`-mb-px inline-flex items-center gap-1.5 border-b-2 px-0.5 py-2.5 text-[14px] font-bold whitespace-nowrap cursor-pointer transition-colors ${focusRing} ${
                tab === k ? 'border-sv-ink text-sv-ink' : 'border-transparent text-sv-muted hover:text-sv-ink'
              }`}
            >
              <i className={icon}></i>
              {label}
              {k === 'proposals' && openCount > 0 && (
                <span className={`ml-0.5 rounded-full px-1.5 text-[11px] font-extrabold ${tone.warn}`}>{openCount}</span>
              )}
            </button>
          ))}
        </nav>

        {tab === 'proposals' && (
          <section className="space-y-3">
            <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-2">
              <div className="flex flex-col gap-2 min-w-0">
                <div className="flex gap-[7px] overflow-x-auto p-0.5" role="group" aria-label="상태 필터">
                  {STATUS_FILTERS.map(f => (
                    <button
                      key={f.key}
                      className={chipCls(statusFilter === f.key)}
                      onClick={() => setStatusFilter(f.key)}
                      aria-pressed={statusFilter === f.key}
                    >
                      {f.label}
                      <span className="opacity-70">{all.filter(p => f.match(p.status ?? '')).length}</span>
                    </button>
                  ))}
                </div>
                <div className="flex gap-[7px] overflow-x-auto p-0.5" role="group" aria-label="유형 필터">
                  {TYPE_FILTERS.map(t => (
                    <button
                      key={t}
                      className={chipCls(typeFilter === t)}
                      onClick={() => setTypeFilter(t)}
                      aria-pressed={typeFilter === t}
                    >
                      {t === 'all' ? '모든 유형' : PROPOSAL_TYPE_LABELS[t]}
                      <span className="opacity-70">
                        {t === 'all' ? byStatus.length : byStatus.filter(p => p.proposal_type === t).length}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
              <span className="text-[12px] text-sv-muted whitespace-nowrap lg:pt-2.5">최근 감사 변경 {fmtDateTime(updatedAt)}</span>
            </div>

            {proposals.loading && !all.length ? (
              <div className={`${panel} animate-pulse`}>
                {[0, 1].map(i => (
                  <div key={i} className="h-40 border-b border-sv-line-soft last:border-b-0 bg-sv-surface-2" />
                ))}
              </div>
            ) : proposals.error ? (
              <div className={`${panel} px-5 py-12 text-center`}>
                <p className="text-sm font-bold text-sv-bad-fg">감사 제안을 불러오지 못했습니다.</p>
                <p className={`mt-2 inline-block ${errorBox}`}>{proposals.error}</p>
              </div>
            ) : visible.length === 0 ? (
              <div className={`${panel} px-5 py-12 text-center`}>
                <p className="text-sm font-bold text-sv-ink">현재 조건에 해당하는 감사 제안이 없습니다.</p>
                <p className="mt-1 text-[13px] text-sv-muted">감사 에이전트는 매일 07:40 KST 에 점검하고, sync 잡이 08:20 에 가져옵니다.</p>
              </div>
            ) : (
              <div className={`${panel} divide-y divide-sv-line-soft`} aria-live="polite">
                {visible.map(p => (
                  <ProposalCard
                    key={p.proposal_id}
                    proposal={p}
                    error={cardErrors[p.proposal_id] ?? null}
                    highlight={highlights[p.proposal_id] ?? []}
                    onApprove={() => setModal({ kind: 'approve', proposal: p })}
                    onReject={() => setModal({ kind: 'reject', proposal: p })}
                    onAck={c => setModal({ kind: 'ack', proposal: p, conflict: c })}
                  />
                ))}
              </div>
            )}
          </section>
        )}

        {tab === 'runs' && <RunsPanel monitoring={monRuns} audit={audRuns} />}

        {tab === 'integrity' && (
          <IntegrityPanel report={integrity.data} loading={integrity.loading} error={integrity.error} />
        )}
      </SvMain>

      {modal && modal.kind !== 'ack' && (
        <ReasonModal
          title={`${PROPOSAL_TYPE_LABELS[modal.proposal.proposal_type] ?? '감사'} 제안 ${modal.kind === 'approve' ? '승인' : '반려'}`}
          tone={modal.kind === 'approve' ? 'primary' : 'danger'}
          confirmLabel={modal.kind === 'approve' ? '승인' : '반려'}
          description={
            <>
              <p className="text-sv-ink font-bold">{modal.proposal.summary}</p>
              <p className="mt-1">
                {modal.kind === 'approve'
                  ? '승인하면 익일(KST)부터 적용 예정으로 바뀌고, 서버가 근거 기준을 다시 검증합니다.'
                  : '반려는 충돌 확인 여부와 무관하게 가능하며 되돌릴 수 없습니다.'}
              </p>
            </>
          }
          onClose={() => setModal(null)}
          onSubmit={reason => doDecision(modal.proposal, modal.kind as 'approve' | 'reject', reason)}
        />
      )}
      {modal && modal.kind === 'ack' && (
        <ReasonModal
          title={`충돌 #${modal.conflict.index} 확인`}
          confirmLabel="확인 기록"
          description={
            <>
              <p className="rounded-lg border border-sv-warn-line bg-sv-warn-soft px-3 py-2.5 text-sv-ink-2 whitespace-pre-wrap">
                {modal.conflict.text}
              </p>
              <p className="mt-2">
                이 충돌을 검토했고 승인을 막을 사유가 아님을 기록합니다. 확인은 지금 문구에 대해서만 유효하며, 문구가
                바뀌면 다시 확인해야 합니다.
              </p>
            </>
          }
          onClose={() => setModal(null)}
          onSubmit={reason => doAck(modal.proposal, modal.conflict, reason)}
        />
      )}
      <Toast toast={toast} />
    </SvPage>
  );
}
