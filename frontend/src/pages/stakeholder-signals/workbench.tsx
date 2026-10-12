import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  fetchOverview,
  fetchStatements,
  gradeClassOf,
  GRADE_CLASS_LABELS,
  headlineQuoteOf,
  ORG_FILTERS,
  topicsFrom,
  type SVGradeClass,
  type SVOverview,
  type SVStatementRow,
} from '@/api/stakeholder';
import SignalsHeader from './components/SignalsHeader';
import PersonAvatar from './components/PersonAvatar';
import { GradeBadge, LocatorMatchBadge } from './components/GradeBadge';
import { StatementDetailPanel } from './components/StatementDrawer';
import { closeBtn, focusRing, linkBtn, scrimStrong } from './theme/sv';
import { SvPage } from './theme/SvPage';

// Stakeholder Signals — 분석가 워크벤치 (스케치 002 이식).
// 좌: 필터(기관·주제·증거·기간) / 중: 주제 레인 스트림 / 우: 발언 상세(모바일은 모달).
// 기관 분류는 서버 ORG_PATTERNS 가 권위 — 기관별 `org=` 조회 결과로 statement_id → 기관 집합을 만든다
// (클라이언트에 패턴을 복제하지 않는다). 주제·증거 필터는 행 필드(topic, grade_class)로 클라이언트에서 거른다.
// "검증 큐"·"내보내기"·검색창 은 사용자 결정(편집·검증 큐 보류)에 따라 두지 않는다.
// 스케치 002 레이아웃: 흰 상단 바 / 좌 248px 필터(우측 경계선) · 중앙 #fafafa 스트림 · 우 390px 상세(좌측 경계선),
// 넓은 화면에서는 세 칸이 각자 스크롤된다.

const OTHER_ORG = '__other__';
const ORG_OPTIONS = [...ORG_FILTERS, { key: OTHER_ORG, label: '기타(국회·학계 등)' }];
const GRADE_OPTIONS: SVGradeClass[] = ['a', 'b', 'c', 'role'];

function fmtDate(s?: string | null): string {
  if (!s) return '—';
  return s.slice(0, 10).replace(/-/g, '.');
}

function firstSentence(s?: string | null): string {
  const t = (s || '').trim();
  if (!t) return '';
  const m = t.match(/^(.+?[.!?。])(\s|$)/);
  return (m ? m[1] : t).slice(0, 160);
}

function useIsDesktop(): boolean {
  const q = '(min-width: 1280px)';
  const [ok, setOk] = useState(() => (typeof window !== 'undefined' ? window.matchMedia(q).matches : true));
  useEffect(() => {
    const mq = window.matchMedia(q);
    const on = () => setOk(mq.matches);
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, []);
  return ok;
}

interface WbData {
  rows: SVStatementRow[];
  orgsById: Record<string, string[]>;
}

async function loadWorkbench(): Promise<WbData> {
  const [all, ...perOrg] = await Promise.all([
    fetchStatements(),
    ...ORG_FILTERS.map(o => fetchStatements({ org: o.key })),
  ]);
  const orgsById: Record<string, string[]> = {};
  perOrg.forEach((items, i) => {
    items.forEach(r => {
      (orgsById[r.statement_id] ||= []).push(ORG_FILTERS[i].key);
    });
  });
  all.forEach(r => {
    if (!orgsById[r.statement_id]) orgsById[r.statement_id] = [OTHER_ORG];
  });
  return { rows: all, orgsById };
}

function CheckRow({
  label,
  checked,
  count,
  onChange,
}: {
  label: string;
  checked: boolean;
  count?: number;
  onChange: () => void;
}) {
  return (
    <label className="flex items-center justify-between gap-2 py-[7px] text-[13px] text-sv-text cursor-pointer hover:text-sv-ink">
      <span className="truncate">{label}</span>
      <span className="flex items-center gap-1.5 flex-shrink-0">
        <input type="checkbox" checked={checked} onChange={onChange} className="accent-sv-ink cursor-pointer w-[15px] h-[15px]" />
        {typeof count === 'number' && <i className="w-4 text-[11px] text-sv-muted">{count}</i>}
      </span>
    </label>
  );
}

function toggle(set: Set<string>, k: string): Set<string> {
  const n = new Set(set);
  if (n.has(k)) n.delete(k);
  else n.add(k);
  return n;
}

export default function StakeholderWorkbenchPage() {
  const [overview, setOverview] = useState<SVOverview | null>(null);
  const [data, setData] = useState<WbData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  const [orgs, setOrgs] = useState<Set<string>>(() => new Set(ORG_OPTIONS.map(o => o.key)));
  const [topics, setTopics] = useState<Set<string> | null>(null); // null = 전체(주제 목록 확정 전)
  const [grades, setGrades] = useState<Set<string>>(() => new Set(GRADE_OPTIONS));
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [mobileOpen, setMobileOpen] = useState(false);
  const isDesktop = useIsDesktop();

  useEffect(() => {
    let cancelled = false;
    fetchOverview()
      .then(o => !cancelled && setOverview(o))
      .catch(() => {/* 헤더 스탬프·기간만 비게 둔다 */});
    return () => {
      cancelled = true;
    };
  }, [tick]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    loadWorkbench()
      .then(d => {
        if (!cancelled) setData(d);
      })
      .catch(() => {
        if (!cancelled) setError('불러오지 못했습니다.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [tick]);

  const { list: topicList, labels: topicLabels } = useMemo(() => topicsFrom(overview), [overview]);
  const activeTopics = useMemo(() => topics ?? new Set(topicList.map(t => t.slug)), [topics, topicList]);
  const rows = useMemo(() => data?.rows ?? [], [data]);

  const orgCounts = useMemo(() => {
    const c: Record<string, number> = {};
    rows.forEach(r => (data?.orgsById[r.statement_id] ?? []).forEach(o => (c[o] = (c[o] ?? 0) + 1)));
    return c;
  }, [rows, data]);

  const filtered = useMemo(
    () =>
      rows.filter(r => {
        const rOrgs = data?.orgsById[r.statement_id] ?? [OTHER_ORG];
        if (!rOrgs.some(o => orgs.has(o))) return false;
        if (r.topic && !activeTopics.has(r.topic)) return false;
        if (!grades.has(gradeClassOf(r.evidence_grade, r.grade_class))) return false;
        return true;
      }),
    [rows, data, orgs, activeTopics, grades],
  );

  const lanes = useMemo(
    () =>
      topicList
        .filter(t => activeTopics.has(t.slug))
        .map(t => {
          const items = filtered.filter(r => r.topic === t.slug);
          const events = new Set(items.map(r => r.event?.event_id).filter(Boolean));
          return { ...t, items, eventCount: events.size };
        }),
    [topicList, activeTopics, filtered],
  );

  // 데스크톱: 필터 결과의 첫 발언을 기본 선택(선택이 필터 밖으로 빠지면 다시 첫 항목)
  useEffect(() => {
    if (!isDesktop) return;
    const first = lanes.find(l => l.items.length > 0)?.items[0];
    if (!selectedId || !filtered.some(r => r.statement_id === selectedId)) {
      setSelectedId(first?.statement_id ?? null);
    }
  }, [isDesktop, lanes, filtered, selectedId]);

  const select = (id: string) => {
    setSelectedId(id);
    if (!isDesktop) setMobileOpen(true);
  };
  const closeMobile = useCallback(() => setMobileOpen(false), []);
  const selected = useMemo(() => rows.find(r => r.statement_id === selectedId) ?? null, [rows, selectedId]);

  useEffect(() => {
    if (!mobileOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeMobile();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [mobileOpen, closeMobile]);

  const scope = overview?.scope;
  const groupCls = 'pb-4 lg:mb-4 lg:border-b border-sv-line-soft min-w-0';
  const groupTitle = 'text-[12px] font-extrabold text-sv-ink mt-1 mb-1.5';

  return (
    <SvPage className="!bg-sv-surface">
      <SignalsHeader view="workbench" overview={overview} />

      <div className="grid grid-cols-1 lg:grid-cols-[248px_minmax(0,1fr)] xl:grid-cols-[248px_minmax(0,1fr)_390px] xl:h-[calc(100vh-62px)]">
        {/* 좌 필터 — 좁은 화면에서는 위쪽 4칸 그리드 */}
        <aside className="border-b lg:border-b-0 lg:border-r border-sv-line px-4 sm:px-5 py-5 grid grid-cols-2 md:grid-cols-4 lg:block gap-x-5 xl:overflow-y-auto">
          <div className={groupCls}>
            <h3 className={groupTitle}>기관</h3>
            {ORG_OPTIONS.map(o => (
              <CheckRow
                key={o.key}
                label={o.label}
                checked={orgs.has(o.key)}
                count={data ? orgCounts[o.key] ?? 0 : undefined}
                onChange={() => setOrgs(s => toggle(s, o.key))}
              />
            ))}
          </div>
          <div className={groupCls}>
            <h3 className={groupTitle}>주제</h3>
            {topicList.map(t => (
              <CheckRow
                key={t.slug}
                label={t.label}
                checked={activeTopics.has(t.slug)}
                onChange={() => setTopics(toggle(activeTopics, t.slug))}
              />
            ))}
          </div>
          <div className={groupCls}>
            <h3 className={groupTitle}>증거 상태</h3>
            {GRADE_OPTIONS.map(g => (
              <CheckRow
                key={g}
                label={GRADE_CLASS_LABELS[g]}
                checked={grades.has(g)}
                onChange={() => setGrades(s => toggle(s, g))}
              />
            ))}
          </div>
          <div className={groupCls}>
            <h3 className={groupTitle}>기간</h3>
            <p className="py-[7px] text-[13px] text-sv-text">
              {scope?.start || scope?.end ? `${fmtDate(scope?.start)} — ${fmtDate(scope?.end)}` : '—'}
            </p>
            <p className="text-[11px] text-sv-muted">공개자료 기준 조사기간</p>
          </div>
        </aside>

        {/* 중앙 주제 레인 */}
        <main className="min-w-0 bg-sv-surface-2 px-4 sm:px-6 py-[22px] xl:overflow-y-auto">
          <div className="mb-[18px]">
            <h1 className="text-[23px] font-extrabold tracking-[-1px] text-sv-ink mb-1">주제별 발언 스트림</h1>
            <p className="text-[13px] text-sv-muted sv-ko">
              동일 발언은 관련 주제에 교차 표시하며, 행사 수는 레인 안에서 중복 없이 셉니다.
            </p>
          </div>

          {loading && (
            <div className="space-y-2 animate-pulse" aria-busy="true">
              {[0, 1, 2, 3].map(i => (
                <div key={i} className="h-[60px] rounded-lg bg-sv-surface shadow-sv-card" />
              ))}
            </div>
          )}
          {!loading && error && (
            <div className="rounded-lg bg-sv-surface shadow-sv-card py-10 text-center">
              <p className="text-sm font-semibold text-sv-bad-fg">발언 목록을 {error}</p>
              <button onClick={() => setTick(t => t + 1)} className={`mt-2 text-sm ${linkBtn}`}>
                다시 시도
              </button>
            </div>
          )}
          {!loading && !error && rows.length === 0 && (
            <p className="rounded-lg bg-sv-surface shadow-sv-card py-10 text-center text-sm text-sv-muted">
              아직 동기화된 발언이 없습니다.
            </p>
          )}
          {!loading && !error && rows.length > 0 && filtered.length === 0 && (
            <p className="rounded-lg bg-sv-surface shadow-sv-card py-10 text-center text-sm text-sv-muted">
              필터 조건에 맞는 발언이 없습니다.
            </p>
          )}

          {!loading &&
            !error &&
            lanes
              .filter(l => l.items.length > 0)
              .map(lane => (
                <section key={lane.slug} className="mb-[22px]">
                  <div className="mb-2.5 flex items-baseline justify-between gap-2">
                    <h2 className="text-[13px] font-extrabold text-sv-ink">{lane.label}</h2>
                    <span className="flex-shrink-0 text-[12px] text-sv-muted">
                      발언 {lane.items.length} · 행사 {lane.eventCount}
                    </span>
                  </div>
                  <div className="space-y-2">
                    {lane.items.map(r => {
                      const hq = headlineQuoteOf(r);
                      const headline = firstSentence(r.summary_text);
                      const active = selectedId === r.statement_id;
                      const mismatch = (hq?.locator_match || '').toLowerCase() === 'none';
                      return (
                        <article
                          key={r.statement_id}
                          role="button"
                          tabIndex={0}
                          aria-label={`${r.speaker_name || '발언자'} 발언 상세 보기`}
                          aria-pressed={active}
                          onClick={() => select(r.statement_id)}
                          onKeyDown={e => {
                            if (e.key === 'Enter' || e.key === ' ') {
                              e.preventDefault();
                              select(r.statement_id);
                            }
                          }}
                          className={`grid grid-cols-[72px_minmax(0,1fr)_auto] min-[1800px]:grid-cols-[80px_minmax(0,170px)_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-2 rounded-lg bg-sv-surface px-3.5 py-[13px] cursor-pointer transition-shadow ${focusRing} ${
                            active
                              ? 'shadow-[0_0_0_2px_var(--sv-focus)]'
                              : mismatch
                                ? 'shadow-[0_0_0_1px_var(--sv-bad-line)] hover:shadow-[0_0_0_2px_var(--sv-focus)]'
                                : 'shadow-sv-card hover:shadow-[0_0_0_2px_var(--sv-focus)]'
                          }`}
                        >
                          <span className="text-[12px] font-mono text-sv-text">{fmtDate(r.event?.event_date)}</span>
                          <span className="flex items-center gap-2 min-w-0">
                            <PersonAvatar
                              name={r.person?.name ?? r.speaker_name}
                              personId={r.person?.person_id}
                              hasPhoto={r.person?.photo}
                              size="sm"
                            />
                            <span className="min-w-0">
                              <b className="block text-[13px] text-sv-ink truncate">{r.speaker_name || '—'}</b>
                              <small className="block text-[11px] text-sv-text truncate">
                                {r.speaker_role_at_event || '직책 미기재'}
                              </small>
                            </span>
                          </span>
                          <span className="justify-self-end min-[1800px]:order-last">
                            <GradeBadge grade={r.evidence_grade} gradeClass={r.grade_class} compact />
                          </span>
                          <span className="min-w-0 sv-ko col-start-2 col-span-2 min-[1800px]:col-start-auto min-[1800px]:col-span-1">
                            {headline ? (
                              <span className="block text-[13px] leading-[1.55] text-sv-ink-2 line-clamp-2">
                                <span className="mr-1.5 rounded bg-sv-neutral-bg px-1.5 py-px text-[10px] font-bold text-sv-neutral-fg">
                                  요약
                                </span>
                                {headline}
                              </span>
                            ) : hq ? (
                              <span
                                className="block text-[13px] leading-[1.55] text-sv-ink line-clamp-2 whitespace-pre-wrap"
                                data-verbatim="true"
                              >
                                {hq.quote_text}
                              </span>
                            ) : (
                              <span className="text-[12px] text-sv-muted">—</span>
                            )}
                            {mismatch && (
                              <span className="mt-1 inline-block">
                                <LocatorMatchBadge match="none" />
                              </span>
                            )}
                          </span>
                        </article>
                      );
                    })}
                  </div>
                </section>
              ))}
        </main>

        {/* 우측 상세 (xl 이상) — 스케치 .detail */}
        {isDesktop && (
          <aside className="border-l border-sv-line bg-sv-surface p-[22px] xl:overflow-y-auto">
            <div className="mb-4 flex items-center justify-between text-[11px] text-sv-muted">
              <span>발언 상세</span>
              {selected?.evidence_grade && <span>{selected.evidence_grade}</span>}
            </div>
            {selectedId ? (
              <StatementDetailPanel
                statementId={selectedId}
                copyrightNotice={overview?.copyright_notice}
                topicLabels={topicLabels}
                variant="workbench"
              />
            ) : (
              <p className="py-10 text-center text-sm text-sv-muted">발언을 선택하면 상세가 표시됩니다.</p>
            )}
          </aside>
        )}
      </div>

      {/* 좁은 화면: 상세는 하단 시트/모달 */}
      {!isDesktop && mobileOpen && selectedId && (
        <div className={`fixed inset-0 z-[60] flex items-end sm:items-center justify-center ${scrimStrong} p-0 sm:p-4`} role="dialog" aria-modal="true" aria-label="발언 상세">
          <div className="absolute inset-0" onClick={closeMobile} />
          <div className="relative w-full sm:max-w-xl max-h-[92vh] overflow-y-auto rounded-t-xl sm:rounded-xl bg-sv-surface shadow-sv-modal p-5">
            <div className="mb-4 flex items-center justify-between">
              <span className="text-[12px] text-sv-muted">발언 상세</span>
              <button onClick={closeMobile} className={closeBtn}>
                닫기 <i className="ri-close-line"></i>
              </button>
            </div>
            <StatementDetailPanel
              statementId={selectedId}
              copyrightNotice={overview?.copyright_notice}
              topicLabels={topicLabels}
              variant="workbench"
            />
          </div>
        </div>
      )}
    </SvPage>
  );
}
