import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { fetchMe } from '@/utils/authUsers';
import {
  adminIntegrity,
  adminSync,
  fetchRuns,
  fetchSignals,
  fetchSources,
  SVApiError,
  svErrorMessage,
  type SVIntegrityReport,
  type SVSignalList,
  type SVSource,
  type SVSyncResult,
} from '@/api/stakeholder';
import { fmtDot, fmtKoDateTime } from '@/pages/stakeholder-signals/sketch/helpers';
import { Toast } from '@/pages/admin/stakeholder-audit/components/ui';
import { useToast } from '@/pages/admin/stakeholder-audit/components/uiHelpers';
import RunsSection, { type RunsState } from './components/RunsSection';
import IntegritySection from './components/IntegritySection';
import { SIGNAL_STATE, SYNC_ERROR_HINTS } from './labels';
import '@/pages/stakeholder-signals/sketch/base.css';
import '@/pages/stakeholder-signals/sketch/common.css';
import '@/pages/stakeholder-signals/sketch/s004.css';
import '@/pages/stakeholder-signals/sketch/s004.additions.css';
import './ops.css';

// 모니터링 운영 현황 — /admin/stakeholder-ops (admin 전용)
//   스케치 004(모니터링 감사)는 제안 검토 화면만 남기고, 운영 뷰(동기화·런 이력·출처 무결성·정책 시그널 반입)는
//   이 별도 화면으로 옮겼다(사용자 결정 2026-10-17). 스케치에 없는 화면이라 004 의 시각 언어(.sx4)를 그대로 쓰고
//   추가분은 ops.css(.sxops 스코프)에 둔다. 데이터는 모두 기존 API — 대쉬보드는 DB 만 읽는다.

interface Loadable<T> {
  data: T;
  loading: boolean;
  error: string | null;
}

interface SyncSuccess {
  ok: true;
  at: string;
  res: SVSyncResult;
}
interface SyncFailure {
  ok: false;
  at: string;
  message: string;
  code?: string;
  status?: number;
}
type SyncOutcome = SyncSuccess | SyncFailure;

const RUN_LIMIT = 20;

function countsLine(counts?: Record<string, number> | null): string {
  if (!counts) return '—';
  const c = counts;
  const total = Object.entries(c)
    .filter(([k]) => k !== 'no_local' && k !== 'excluded')
    .reduce((n, [, v]) => n + v, 0);
  const extra = [c.no_local ? `로컬 원문 없음 ${c.no_local}` : '', c.excluded ? `의도적 제외 ${c.excluded}` : ''].filter(Boolean);
  return `${c.verified ?? 0}/${total} 일치${extra.length ? ` (${extra.join(' · ')})` : ''}`;
}

/** KST 시:분 */
function hhmm(v: string): string {
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return '';
  return new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(d);
}

/** sync 결과 detail.counts → 사람이 읽는 한 줄 (모양이 다르면 null — 서버 summary 로 대체). */
function syncCountsLine(r: SVSyncResult): string | null {
  const c = r.detail && typeof r.detail === 'object' ? (r.detail as { counts?: unknown }).counts : null;
  if (!c || typeof c !== 'object') return null;
  const n = (k: string) => {
    const v = (c as Record<string, unknown>)[k];
    return typeof v === 'number' ? v : null;
  };
  const parts: [string, number | null][] = [
    ['변경', n('changes')],
    ['목록', n('listed')],
    ['새로 받음', n('downloaded')],
    ['그대로', n('unchanged')],
    ['허용목록 밖', n('rejected')],
    ['받기 실패', n('fetch_failed')],
    ['예약 반영', n('scheduled_applied')],
  ];
  const shown = parts.filter(([, v]) => v !== null).map(([k, v]) => `${k} ${v}`);
  return shown.length ? `sync 완료 — ${shown.join(' · ')}` : null;
}

export default function AdminStakeholderOpsPage() {
  const navigate = useNavigate();
  const [authChecked, setAuthChecked] = useState(false);
  const [integrity, setIntegrity] = useState<Loadable<SVIntegrityReport | null>>({ data: null, loading: true, error: null });
  const [noLocal, setNoLocal] = useState<SVSource[]>([]);
  const [mon, setMon] = useState<RunsState>({ data: [], loading: true, error: null });
  const [aud, setAud] = useState<RunsState>({ data: [], loading: true, error: null });
  const [signals, setSignals] = useState<Loadable<SVSignalList | null>>({ data: null, loading: true, error: null });
  const [syncing, setSyncing] = useState(false);
  const [outcome, setOutcome] = useState<SyncOutcome | null>(null);
  const [toast, showToast] = useToast();

  // admin 셀프 가드 (다른 admin 화면과 같은 패턴) — 비관리자는 홈으로
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

  const loadAll = useCallback(() => {
    setIntegrity(s => ({ ...s, loading: s.data === null, error: null }));
    adminIntegrity()
      .then(d => setIntegrity({ data: d, loading: false, error: null }))
      .catch(e => setIntegrity({ data: null, loading: false, error: svErrorMessage(e) }));
    fetchSources()
      .then(items => setNoLocal(items.filter(s => (s.integrity_status || '').toLowerCase() === 'no_local')))
      .catch(() => setNoLocal([]));
    fetchRuns('monitoring', RUN_LIMIT)
      .then(d => setMon({ data: d, loading: false, error: null }))
      .catch(e => setMon({ data: [], loading: false, error: svErrorMessage(e) }));
    fetchRuns('audit', RUN_LIMIT)
      .then(d => setAud({ data: d, loading: false, error: null }))
      .catch(e => setAud({ data: [], loading: false, error: svErrorMessage(e) }));
    fetchSignals()
      .then(d => setSignals({ data: d, loading: false, error: null }))
      .catch(e => setSignals({ data: null, loading: false, error: svErrorMessage(e, '정책 시그널 상태를 불러오지 못했습니다.') }));
  }, []);

  useEffect(() => {
    if (authChecked) loadAll();
  }, [authChecked, loadAll]);

  const runSync = async () => {
    if (syncing) return;
    setSyncing(true);
    const at = new Date().toISOString();
    try {
      const res = await adminSync();
      setOutcome({ ok: true, at, res });
      showToast('sync 를 마쳤습니다. 화면을 새 데이터로 갱신했습니다.');
      loadAll();
    } catch (e) {
      const code = e instanceof SVApiError ? e.code : undefined;
      const status = e instanceof SVApiError ? e.status : undefined;
      setOutcome({ ok: false, at, message: svErrorMessage(e, 'sync 요청을 처리하지 못했습니다.'), code, status });
      showToast(code && SYNC_ERROR_HINTS[code] ? SYNC_ERROR_HINTS[code] : 'sync 에 실패했습니다.', 'error');
    } finally {
      setSyncing(false);
    }
  };

  if (!authChecked) return <div className="sxbase sx4 sxops" />;

  const rep = integrity.data;
  const sig = signals.data;
  const sigState = (sig?.import_status?.state || (sig ? 'ok' : '')).toLowerCase();
  const sigMeta = SIGNAL_STATE[sigState];
  const srcTotal = rep
    ? Object.entries(rep.counts ?? {})
        .filter(([k]) => k !== 'no_local' && k !== 'excluded')
        .reduce((n, [, v]) => n + v, 0)
    : null;
  const lastResult = outcome?.ok ? (outcome as SyncSuccess).res : null;
  const failure = outcome && !outcome.ok ? (outcome as SyncFailure) : null;

  return (
    <div className="sxbase sx4 sxops">
      <div className="main">
        <div className="top">
          <div className="crumb">약가·건강보험 재정 / 모니터링 운영 현황</div>
          <div className="notice">자동 sync 매일 02:20 · 08:20 KST</div>
        </div>
        <section className="hero">
          <div>
            <h1>모니터링 운영 현황</h1>
            <p>
              소스 repo 동기화, 모니터링·감사 런 이력, 원문 무결성, 정책 시그널 반입 상태를 한곳에서 확인합니다.
              <br />
              감사 제안 승인·반려는{' '}
              <Link className="svx-link" to="/admin/stakeholder-audit">
                모니터링 감사
              </Link>{' '}
              화면에서 합니다.
            </p>
          </div>
          <div className="stats">
            <div className="stat">
              마지막 sync<b>{rep?.last_sync_at ? `${fmtDot(rep.last_sync_at)} ${hhmm(rep.last_sync_at)}` : '—'}</b>
            </div>
            <div className="stat">
              원문 무결성<b>{rep && srcTotal !== null ? `${rep.counts?.verified ?? 0}/${srcTotal}` : '—'}</b>
            </div>
          </div>
        </section>

        {/* 1. 동기화 */}
        <div className="sec">
          <div>
            <h2>동기화</h2>
            <p>소스 repo → 볼륨 미러 → sv_* 적재. 같은 스냅샷을 다시 받으면 변경 0건입니다(멱등).</p>
          </div>
        </div>
        <section className="panel sync" aria-live="polite">
          <div>
            <dl className="kv">
              <dt>마지막 sync</dt>
              <dd>{integrity.loading ? '불러오는 중…' : fmtKoDateTime(rep?.last_sync_at)}</dd>
              <dt>원문 출처</dt>
              <dd>{countsLine(rep?.counts)}</dd>
              <dt>산출물</dt>
              <dd>{countsLine(rep?.artifact_counts)}</dd>
              {outcome && (
                <>
                  <dt>이번 수동 sync</dt>
                  <dd>
                    <span className={`svb ${outcome.ok ? 'ok' : 'bad'}`}>{outcome.ok ? '완료' : '실패'}</span>{' '}
                    <span style={{ color: '#777' }}>{fmtKoDateTime(outcome.at)}</span>
                  </dd>
                </>
              )}
            </dl>
            {lastResult && (syncCountsLine(lastResult) || lastResult.summary) && (
              <div className="change">{syncCountsLine(lastResult) || lastResult.summary}</div>
            )}
            {failure && (
              <div className="server-error" role="alert">
                <b>{failure.code && SYNC_ERROR_HINTS[failure.code] ? SYNC_ERROR_HINTS[failure.code] : 'sync 에 실패했습니다.'}</b>
                {failure.message}
                {(failure.code || failure.status) && (
                  <code>
                    {failure.code ?? ''}
                    {failure.status ? ` · HTTP ${failure.status}` : ''}
                  </code>
                )}
              </div>
            )}
            {outcome?.ok && lastResult?.detail && (
              <details>
                <summary>sync 결과 원본</summary>
                <pre className="raw">{JSON.stringify(lastResult.detail, null, 2)}</pre>
              </details>
            )}
          </div>
          <div className="actions">
            <button className="btn primary" onClick={runSync} disabled={syncing} aria-busy={syncing}>
              {syncing ? 'sync 중…' : '지금 sync'}
            </button>
          </div>
        </section>

        {/* 2. 런 이력 */}
        <RunsSection monitoring={mon} audit={aud} />

        {/* 3. 출처 무결성 */}
        <IntegritySection report={rep} loading={integrity.loading} error={integrity.error} noLocal={noLocal} />

        {/* 4. 정책 시그널 반입 */}
        <div className="sec">
          <div>
            <h2>정책 시그널 반입</h2>
            <p>소스 repo 의 data/policy_signals.json 을 sync 때 검증해 반입합니다. 실패하면 마지막 정상본을 유지합니다.</p>
          </div>
        </div>
        <section className="panel signals" aria-live="polite">
          {signals.loading ? (
            <p className="meta" style={{ margin: 0 }}>
              불러오는 중…
            </p>
          ) : signals.error ? (
            <div className="server-error" role="alert" style={{ margin: 0 }}>
              <b>정책 시그널 상태를 불러오지 못했습니다.</b>
              {signals.error}
            </div>
          ) : (
            <>
              <dl className="kv">
                <dt>반입 상태</dt>
                <dd>
                  <span className={`svb ${sigMeta?.[1] ?? 'neutral'}`} title={sigState || undefined}>
                    {sigMeta?.[0] ?? (sigState || '—')}
                  </span>{' '}
                  <span style={{ color: '#777' }}>{sigMeta?.[2] ?? ''}</span>
                </dd>
                <dt>시그널 수</dt>
                <dd>{sig?.signal_count ?? sig?.signals.length ?? 0}건</dd>
                <dt>파일 갱신</dt>
                <dd>{fmtKoDateTime(sig?.updated_at)}</dd>
                {sig?.schema_version != null && (
                  <>
                    <dt>스키마</dt>
                    <dd>v{String(sig.schema_version)}</dd>
                  </>
                )}
              </dl>
              {sig?.import_status?.error && (
                <div className="server-error" role="alert">
                  <b>검증 오류</b>
                  {sig.import_status.error}
                </div>
              )}
            </>
          )}
        </section>
      </div>
      <Toast toast={toast} />
    </div>
  );
}
