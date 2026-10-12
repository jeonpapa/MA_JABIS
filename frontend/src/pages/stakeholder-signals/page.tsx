import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  fetchOverview,
  fetchStatements,
  headlineQuoteOf,
  safeHttpUrl,
  topicsFrom,
  type SVOverview,
  type SVStatementRow,
} from '@/api/stakeholder';
import SignalsHeader from './components/SignalsHeader';
import TopicTabs from './components/TopicTabs';
import StatementDrawer from './components/StatementDrawer';
import PersonAvatar from './components/PersonAvatar';
import { GradeBadge, GradeLegend, IntegrityBadge, LocatorMatchBadge } from './components/GradeBadge';
import { badge, card, eyebrow, focusRing, linkBtn, panel, thead, tone } from './theme/sv';
import { SvMain, SvPage } from './theme/SvPage';

// Stakeholder Signals — 리더십 타임라인 (스케치 001 의 구조·필드·화이트 톤 그대로 이식, .sv-light 스코프).
// 데이터: GET /api/stakeholder/overview · GET /api/stakeholder/statements · GET /api/stakeholder/statements/<id>(드로어)
// 편집 UI("+ 행사 업데이트")는 사용자 결정으로 보류 — 이 화면은 읽기 전용.

type Load<T> = { data: T | null; loading: boolean; error: string | null };

function fmtDate(s?: string | null): string {
  if (!s) return '—';
  return s.slice(0, 10).replace(/-/g, '.');
}

const RUN_STATUS_TONE: Record<string, keyof typeof tone> = {
  ok: 'ok',
  completed: 'ok',
  success: 'ok',
  degraded: 'warn',
  partial: 'warn',
  failed: 'bad',
  error: 'bad',
};

function RunStatusBadge({ status }: { status?: string | null }) {
  if (!status) return null;
  return <span className={`${badge} ${tone[RUN_STATUS_TONE[status.toLowerCase()] ?? 'neutral']}`}>{status}</span>;
}

/** 스케치 .metric — 라벨 12px · 숫자 27px · 보조줄(강조값은 파랑 굵게) */
function KpiCard({ label, value, sub }: { label: string; value?: number | null; sub?: React.ReactNode }) {
  return (
    <div className={`${card} p-[17px] min-w-0`}>
      <p className="text-sv-muted text-[12px]">{label}</p>
      <p className="mt-2 mb-1 text-[27px] font-extrabold tracking-[-1px] leading-none text-sv-ink">
        {typeof value === 'number' ? value : '—'}
      </p>
      {sub && <p className="text-[11px] text-sv-muted sv-ko">{sub}</p>}
    </div>
  );
}

const Em = ({ children }: { children: React.ReactNode }) => <b className="text-sv-accent-ink font-bold">{children}</b>;

/** 전체 탭: 같은 (행사 × 발언자) 가 여러 주제에 교차 표시된 블록을 1행으로 묶는다(행사 중복 집계 금지). */
interface TimelineRow {
  key: string;
  row: SVStatementRow;
  topics: string[];
}

function groupRows(rows: SVStatementRow[], topic: string): TimelineRow[] {
  if (topic !== 'all') {
    return rows.filter(r => r.topic === topic).map(r => ({ key: r.statement_id, row: r, topics: r.topic ? [r.topic] : [] }));
  }
  const out: TimelineRow[] = [];
  const byKey = new Map<string, TimelineRow>();
  rows.forEach(r => {
    const k = `${r.event?.event_id ?? r.statement_id}::${r.speaker_name ?? ''}`;
    const hit = byKey.get(k);
    if (!hit) {
      const g = { key: k, row: r, topics: r.topic ? [r.topic] : [] };
      byKey.set(k, g);
      out.push(g);
      return;
    }
    if (r.topic && !hit.topics.includes(r.topic)) hit.topics.push(r.topic);
    // 대표 행은 인용을 가진 블록 우선
    if (!headlineQuoteOf(hit.row) && headlineQuoteOf(r)) hit.row = r;
  });
  return out;
}

export default function StakeholderSignalsPage() {
  const [ov, setOv] = useState<Load<SVOverview>>({ data: null, loading: true, error: null });
  const [st, setSt] = useState<Load<SVStatementRow[]>>({ data: null, loading: true, error: null });
  const [topic, setTopic] = useState('all');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setOv(s => ({ ...s, loading: true, error: null }));
    setSt(s => ({ ...s, loading: true, error: null }));
    fetchOverview()
      .then(d => !cancelled && setOv({ data: d, loading: false, error: null }))
      .catch(() => !cancelled && setOv({ data: null, loading: false, error: '불러오지 못했습니다.' }));
    fetchStatements()
      .then(d => !cancelled && setSt({ data: d, loading: false, error: null }))
      .catch(() => !cancelled && setSt({ data: null, loading: false, error: '불러오지 못했습니다.' }));
    return () => {
      cancelled = true;
    };
  }, [tick]);

  const overview = ov.data;
  const { list: topicList, labels: topicLabels } = useMemo(() => topicsFrom(overview), [overview]);
  const rows = useMemo(() => st.data ?? [], [st.data]);
  const visible = useMemo(() => groupRows(rows, topic), [rows, topic]);
  const counts = useMemo(() => {
    const c: Record<string, number> = { all: groupRows(rows, 'all').length };
    rows.forEach(r => {
      if (r.topic) c[r.topic] = (c[r.topic] ?? 0) + 1;
    });
    return c;
  }, [rows]);

  const closeDrawer = useCallback(() => setSelectedId(null), []);
  const kpi = overview?.kpi;
  const integ = overview?.integrity;
  const audit = overview?.latest_audit_run;
  const mon = overview?.latest_monitoring_run;
  const recent = overview?.recent_events ?? [];

  return (
    <SvPage>
      <SvMain>
      <SignalsHeader view="timeline" overview={overview} />

      <div className="space-y-[18px]">
        {/* KPI 4 */}
        {ov.loading ? (
          <div className="grid grid-cols-2 xl:grid-cols-4 gap-3 animate-pulse">
            {[0, 1, 2, 3].map(i => (
              <div key={i} className={`h-[104px] ${card}`} />
            ))}
          </div>
        ) : ov.error ? (
          <div className={`${card} p-5 text-center`}>
            <p className="text-sm font-semibold text-sv-bad-fg">요약 지표를 불러오지 못했습니다.</p>
            <button onClick={() => setTick(t => t + 1)} className={`mt-2 text-sm ${linkBtn}`}>
              다시 시도
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-2 xl:grid-cols-4 gap-3">
            <KpiCard
              label="포착 행사"
              value={kpi?.events}
              sub={
                overview?.scope?.start || overview?.scope?.end
                  ? (
                      <>
                        조사기간 <Em>{fmtDate(overview?.scope?.start)} — {fmtDate(overview?.scope?.end)}</Em> · 중복 제거
                      </>
                    )
                  : '정본 타임라인 기준 · 중복 제거'
              }
            />
            <KpiCard
              label="확인 발언(직접인용)"
              value={kpi?.quotes}
              sub={
                <>
                  원문 대조 출처{' '}
                  <Em>
                    {integ?.sources_verified ?? 0} / {integ?.sources_total ?? 0}
                  </Em>{' '}
                  · 발언 블록 {kpi?.statements ?? 0}
                </>
              }
            />
            <KpiCard
              label="핵심 인물"
              value={kpi?.people_active}
              sub={`우선 모니터링 ${kpi?.people_priority ?? 0}명 · 발언자 ${kpi?.speakers_observed ?? 0}명`}
            />
            <KpiCard
              label="후속 확인 필요"
              value={(kpi?.people_candidates ?? 0) + (kpi?.pending_proposals ?? 0)}
              sub={`직책 미확인 후보 ${kpi?.people_candidates ?? 0} · 미검토 제안 ${kpi?.pending_proposals ?? 0}`}
            />
          </div>
        )}

        {/* 시그널 카드 (스케치 .signal: 1.4fr / 1fr) */}
        {!ov.loading && !ov.error && (
          <div className="grid grid-cols-1 xl:grid-cols-[1.4fr_1fr] gap-3">
            <div className={`${card} p-5`}>
              <div className="flex flex-wrap items-center gap-2 mb-2.5">
                <span className={eyebrow}>최근 감사 시그널</span>
                <RunStatusBadge status={audit?.run_status} />
                {audit?.run_date && <span className="text-[11px] text-sv-muted">{fmtDate(audit.run_date)}</span>}
              </div>
              {audit?.claim ? (
                <>
                  <h2 className="text-sv-ink font-extrabold text-[20px] tracking-[-0.6px] leading-snug sv-ko">{audit.claim}</h2>
                  {audit.why_this_matters && (
                    <p className="mt-2.5 text-[14px] leading-[1.7] text-sv-text whitespace-pre-wrap sv-ko">{audit.why_this_matters}</p>
                  )}
                  <p className="mt-2 text-[11px] text-sv-muted">
                    감사 에이전트의 판단(analysis)입니다 — 발언 원문이 아닙니다.
                  </p>
                </>
              ) : (
                <p className="text-sm text-sv-muted">아직 동기화된 감사 런이 없습니다.</p>
              )}
              {mon && (
                <p className="mt-3 border-t border-sv-line-soft pt-2.5 text-[11px] text-sv-muted">
                  최근 모니터링 {fmtDate(mon.run_date)} · <RunStatusBadge status={mon.run_status} /> · 포화{' '}
                  {mon.saturation_status || '—'}
                  {mon.saturation_status === 'partial' && ' (전수 확인 미완료)'} · 신규 인용 {mon.new_quotes ?? 0}
                </p>
              )}
            </div>
            <div className={`${card} px-[17px] py-1`}>
              <p className="pt-3 pb-1 text-[11px] font-bold text-sv-muted">최근 행사</p>
              {recent.length === 0 && <p className="py-3 text-sm text-sv-muted">최근 행사가 없습니다.</p>}
              <ul className="divide-y divide-sv-line-soft">
                {recent.slice(0, 3).map(ev => (
                  <li key={ev.event_id} className="py-3 flex items-start justify-between gap-3">
                    <span className="min-w-0">
                      <b className="block text-[13px] font-bold text-sv-ink leading-snug sv-ko">{ev.title || '—'}</b>
                      {(ev.speakers?.length ?? 0) > 0 && (
                        <small className="block text-[12px] text-sv-text truncate">{ev.speakers?.join(', ')}</small>
                      )}
                    </span>
                    <span className="flex-shrink-0 text-[11px] text-sv-muted">{fmtDate(ev.event_date)}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}

        {/* 주제 탭 + 범례 */}
        <div className="space-y-2.5 pt-1.5">
          <TopicTabs topics={topicList} value={topic} counts={st.data ? counts : undefined} onChange={setTopic} />
          <GradeLegend />
        </div>

        {/* 행 테이블 */}
        <section className={panel}>
          <div className={`hidden lg:grid grid-cols-[92px_130px_minmax(0,1fr)_170px_minmax(0,2fr)_150px] gap-3 px-4 py-[11px] ${thead}`}>
            <div>행사일</div>
            <div>주제</div>
            <div>행사</div>
            <div>인물·당시 직책</div>
            <div>확인된 발언</div>
            <div>증거</div>
          </div>

          {st.loading && (
            <div className="p-5 space-y-3 animate-pulse" aria-busy="true">
              {[0, 1, 2, 3].map(i => (
                <div key={i} className="h-12 rounded-lg bg-sv-surface-3" />
              ))}
            </div>
          )}
          {!st.loading && st.error && (
            <div className="py-10 text-center">
              <p className="text-sm font-semibold text-sv-bad-fg">발언 목록을 {st.error}</p>
              <button onClick={() => setTick(t => t + 1)} className={`mt-2 text-sm ${linkBtn}`}>
                다시 시도
              </button>
            </div>
          )}
          {!st.loading && !st.error && visible.length === 0 && (
            <p className="py-10 text-center text-sm text-sv-muted">
              {rows.length === 0 ? '아직 동기화된 발언이 없습니다.' : '이 주제에 해당하는 발언이 없습니다.'}
            </p>
          )}
          {!st.loading &&
            !st.error &&
            visible.map(({ key, row, topics }) => {
              const hq = headlineQuoteOf(row);
              const srcHref = safeHttpUrl(row.source?.url);
              const mismatch = (hq?.locator_match || '').toLowerCase() === 'none';
              const open = () => setSelectedId(row.statement_id);
              return (
                <div
                  key={key}
                  role="button"
                  tabIndex={0}
                  aria-label={`${row.speaker_name || '발언자'} 발언 상세 보기`}
                  onClick={open}
                  onKeyDown={e => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      open();
                    }
                  }}
                  className={`group grid grid-cols-[76px_minmax(0,1fr)] lg:grid-cols-[92px_130px_minmax(0,1fr)_170px_minmax(0,2fr)_150px] [&>*:nth-child(n+3)]:col-start-2 lg:[&>*:nth-child(n+3)]:col-start-auto items-center gap-x-2.5 gap-y-2 lg:gap-3 px-4 py-[15px] border-t border-sv-line odd:bg-sv-zebra cursor-pointer hover:bg-sv-hover focus:outline focus:outline-2 focus:-outline-offset-2 focus:outline-sv-focus focus:bg-sv-selected transition-colors sv-ko ${
                    mismatch ? 'lg:shadow-[inset_3px_0_0_var(--sv-bad-strong)]' : ''
                  }`}
                >
                  <div className="text-[12px] font-mono text-sv-ink self-start lg:self-center">{fmtDate(row.event?.event_date)}</div>
                  <div className="flex flex-wrap gap-1 content-start">
                    {topics.map(t => (
                      <span key={t} className="text-[12px] font-bold text-sv-ink leading-snug">
                        {topicLabels[t] ?? t}
                      </span>
                    ))}
                  </div>
                  <div className="text-[13px] text-sv-ink-2 leading-snug">{row.event?.title || '—'}</div>
                  <div className="flex items-center gap-2.5 min-w-0">
                    <PersonAvatar
                      name={row.person?.name ?? row.speaker_name}
                      personId={row.person?.person_id}
                      hasPhoto={row.person?.photo}
                      size="md"
                    />
                    <span className="min-w-0">
                      <b className="block text-[14px] text-sv-ink truncate">{row.speaker_name || '—'}</b>
                      <small className="block text-[11px] leading-[1.35] text-sv-text line-clamp-2" title={row.speaker_role_at_event || ''}>
                        {row.speaker_role_at_event || '직책 미기재'}
                      </small>
                    </span>
                  </div>
                  <div className="min-w-0">
                    {hq ? (
                      <>
                        <p
                          className="text-[13.5px] leading-[1.55] text-sv-ink-2 line-clamp-3 whitespace-pre-wrap"
                          data-verbatim="true"
                        >
                          {hq.quote_text}
                        </p>
                        {mismatch && (
                          <div className="mt-1">
                            <LocatorMatchBadge match="none" />
                          </div>
                        )}
                      </>
                    ) : row.summary_text ? (
                      <p className="text-[12.5px] italic leading-[1.55] text-sv-text line-clamp-3">
                        <span className="not-italic mr-1.5 rounded bg-sv-neutral-bg px-1.5 py-px text-[10px] font-bold text-sv-neutral-fg">
                          요약
                        </span>
                        {row.summary_text}
                      </p>
                    ) : (
                      <p className="text-[12px] text-sv-muted">—</p>
                    )}
                    {row.source && (
                      <p className="mt-1 text-[11px] text-sv-muted truncate">
                        출처{' '}
                        {srcHref ? (
                          <a
                            href={srcHref}
                            target="_blank"
                            rel="noopener noreferrer"
                            onClick={e => e.stopPropagation()}
                            className={`text-sv-accent-ink hover:underline ${focusRing}`}
                          >
                            {row.source.publisher_title || row.source.source_id}
                          </a>
                        ) : (
                          row.source.publisher_title || row.source.source_id
                        )}
                        {row.source.publication_date && ` · ${fmtDate(row.source.publication_date)}`}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center justify-between gap-1.5">
                    <div className="flex flex-wrap lg:flex-col items-start gap-1">
                      <GradeBadge grade={row.evidence_grade} gradeClass={row.grade_class} compact />
                      {row.source && <IntegrityBadge status={row.source.integrity_status} />}
                    </div>
                    <span
                      aria-hidden="true"
                      className="hidden lg:block text-[18px] text-sv-muted opacity-0 group-hover:opacity-100 group-focus:opacity-100 transition-opacity"
                    >
                      ›
                    </span>
                  </div>
                </div>
              );
            })}
        </section>

        <p className="text-[11px] leading-relaxed text-sv-muted">
          <i className="ri-copyright-line mr-1"></i>
          {overview?.copyright_notice ||
            '원문 저작권은 각 발행기관·언론사에 있습니다. 내부 검증과 출처(provenance) 보존 목적으로만 발췌를 제공합니다.'}
        </p>
      </div>
      </SvMain>

      {selectedId && (
        <StatementDrawer
          statementId={selectedId}
          copyrightNotice={overview?.copyright_notice}
          topicLabels={topicLabels}
          onClose={closeDrawer}
        />
      )}
    </SvPage>
  );
}
