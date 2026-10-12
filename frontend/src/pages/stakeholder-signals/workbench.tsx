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

// Stakeholder Signals — 분석가 워크벤치 (스케치 002 이식).
// 좌: 필터(기관·주제·증거·기간) / 중: 주제 레인 스트림 / 우: 발언 상세(모바일은 모달).
// 기관 분류는 서버 ORG_PATTERNS 가 권위 — 기관별 `org=` 조회 결과로 statement_id → 기관 집합을 만든다
// (클라이언트에 패턴을 복제하지 않는다). 주제·증거 필터는 행 필드(topic, grade_class)로 클라이언트에서 거른다.
// "검증 큐"·"내보내기" 는 사용자 결정으로 보류.

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
    <label className="flex items-center justify-between gap-2 py-1 text-[12px] text-[#C9D1DC] cursor-pointer">
      <span className="flex items-center gap-2 min-w-0">
        <input type="checkbox" checked={checked} onChange={onChange} className="accent-[#00E5CC] cursor-pointer" />
        <span className="truncate">{label}</span>
      </span>
      {typeof count === 'number' && <span className="text-[10px] text-[#4A5568]">{count}</span>}
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

  useEffect(() => {
    if (!mobileOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeMobile();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [mobileOpen, closeMobile]);

  const scope = overview?.scope;

  return (
    <div className="min-h-screen bg-[#0D1117] text-white">
      <SignalsHeader view="workbench" overview={overview} />

      <div className="px-8 py-6 grid grid-cols-1 lg:grid-cols-[220px_minmax(0,1fr)] xl:grid-cols-[220px_minmax(0,1fr)_420px] gap-5 items-start">
        {/* 좌 필터 */}
        <aside className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-4 space-y-5 lg:sticky lg:top-4">
          <div>
            <h3 className="text-[10px] font-semibold uppercase tracking-wider text-[#4A5568] mb-1.5">기관</h3>
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
          <div>
            <h3 className="text-[10px] font-semibold uppercase tracking-wider text-[#4A5568] mb-1.5">주제</h3>
            {topicList.map(t => (
              <CheckRow
                key={t.slug}
                label={t.label}
                checked={activeTopics.has(t.slug)}
                onChange={() => setTopics(toggle(activeTopics, t.slug))}
              />
            ))}
          </div>
          <div>
            <h3 className="text-[10px] font-semibold uppercase tracking-wider text-[#4A5568] mb-1.5">증거 등급</h3>
            {GRADE_OPTIONS.map(g => (
              <CheckRow
                key={g}
                label={GRADE_CLASS_LABELS[g]}
                checked={grades.has(g)}
                onChange={() => setGrades(s => toggle(s, g))}
              />
            ))}
          </div>
          <div>
            <h3 className="text-[10px] font-semibold uppercase tracking-wider text-[#4A5568] mb-1.5">기간</h3>
            <p className="text-[12px] text-[#C9D1DC]">
              {scope?.start || scope?.end ? `${fmtDate(scope?.start)} — ${fmtDate(scope?.end)}` : '—'}
            </p>
            <p className="mt-0.5 text-[10px] text-[#4A5568]">공개자료 기준 조사기간</p>
          </div>
        </aside>

        {/* 중앙 주제 레인 */}
        <main className="space-y-4 min-w-0">
          <div>
            <h2 className="text-white font-bold text-base">주제별 발언 스트림</h2>
            <p className="text-[11px] text-[#4A5568]">
              같은 발언이 여러 주제에 걸치면 각 주제에 교차 표시합니다. 행사 수는 레인 안에서 중복 없이 셉니다.
            </p>
          </div>

          {loading && (
            <div className="space-y-3 animate-pulse" aria-busy="true">
              {[0, 1, 2].map(i => (
                <div key={i} className="h-28 rounded-2xl bg-[#161B27] border border-[#1E2530]" />
              ))}
            </div>
          )}
          {!loading && error && (
            <div className="rounded-2xl border border-[#1E2530] bg-[#161B27] py-10 text-center">
              <p className="text-sm text-red-400">발언 목록을 {error}</p>
              <button onClick={() => setTick(t => t + 1)} className="mt-2 text-sm text-[#00E5CC] hover:underline cursor-pointer">
                다시 시도
              </button>
            </div>
          )}
          {!loading && !error && rows.length === 0 && (
            <p className="rounded-2xl border border-[#1E2530] bg-[#161B27] py-10 text-center text-sm text-[#4A5568]">
              아직 동기화된 발언이 없습니다.
            </p>
          )}
          {!loading && !error && rows.length > 0 && filtered.length === 0 && (
            <p className="rounded-2xl border border-[#1E2530] bg-[#161B27] py-10 text-center text-sm text-[#4A5568]">
              필터 조건에 맞는 발언이 없습니다.
            </p>
          )}

          {!loading &&
            !error &&
            lanes
              .filter(l => l.items.length > 0)
              .map(lane => (
                <section key={lane.slug} className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-4">
                  <div className="mb-3 flex items-baseline justify-between gap-2">
                    <h3 className="text-[13px] font-bold text-white">{lane.label}</h3>
                    <span className="text-[10px] text-[#4A5568]">
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
                          className={`grid grid-cols-[76px_minmax(0,170px)_minmax(0,1fr)_auto] items-center gap-3 rounded-xl border px-3 py-2.5 cursor-pointer transition-colors focus:outline-none ${
                            active
                              ? 'border-[#00E5CC]/60 bg-[#00E5CC]/5'
                              : 'border-[#1E2530] bg-[#0D1117] hover:border-[#2A3545] focus:border-[#2A3545]'
                          }`}
                        >
                          <span className="text-[11px] font-mono text-[#8B9BB4]">{fmtDate(r.event?.event_date)}</span>
                          <span className="flex items-center gap-2 min-w-0">
                            <PersonAvatar
                              name={r.person?.name ?? r.speaker_name}
                              personId={r.person?.person_id}
                              hasPhoto={r.person?.photo}
                              size="sm"
                            />
                            <span className="min-w-0">
                              <b className="block text-[12px] text-white truncate">{r.speaker_name || '—'}</b>
                              <small className="block text-[10px] text-[#8B9BB4] truncate">
                                {r.speaker_role_at_event || '직책 미기재'}
                              </small>
                            </span>
                          </span>
                          <span className="min-w-0">
                            {headline ? (
                              <span className="block text-[12px] leading-snug text-[#C9D1DC] line-clamp-2">
                                <span className="mr-1 rounded bg-[#1E2530] px-1 py-px text-[9px] font-semibold text-[#4A5568]">
                                  요약
                                </span>
                                {headline}
                              </span>
                            ) : hq ? (
                              <span
                                className="block font-serif text-[12px] leading-snug text-white line-clamp-2 whitespace-pre-wrap"
                                data-verbatim="true"
                              >
                                {hq.quote_text}
                              </span>
                            ) : (
                              <span className="text-[12px] text-[#4A5568]">—</span>
                            )}
                            {mismatch && (
                              <span className="mt-1 inline-block">
                                <LocatorMatchBadge match="none" />
                              </span>
                            )}
                          </span>
                          <GradeBadge grade={r.evidence_grade} gradeClass={r.grade_class} compact />
                        </article>
                      );
                    })}
                  </div>
                </section>
              ))}
        </main>

        {/* 우측 상세 (xl 이상) */}
        {isDesktop && (
          <aside className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5 xl:sticky xl:top-4 xl:max-h-[calc(100vh-2rem)] xl:overflow-y-auto">
            <p className="mb-3 text-[11px] font-semibold uppercase tracking-wider text-[#4A5568]">발언 상세</p>
            {selectedId ? (
              <StatementDetailPanel
                statementId={selectedId}
                copyrightNotice={overview?.copyright_notice}
                topicLabels={topicLabels}
                variant="workbench"
              />
            ) : (
              <p className="py-10 text-center text-sm text-[#4A5568]">발언을 선택하면 상세가 표시됩니다.</p>
            )}
          </aside>
        )}
      </div>

      {/* 좁은 화면: 상세는 모달 */}
      {!isDesktop && mobileOpen && selectedId && (
        <div className="fixed inset-0 z-[60] flex items-end sm:items-center justify-center bg-black/60 p-0 sm:p-4" role="dialog" aria-modal="true" aria-label="발언 상세">
          <div className="absolute inset-0" onClick={closeMobile} />
          <div className="relative w-full sm:max-w-xl max-h-[92vh] overflow-y-auto rounded-t-2xl sm:rounded-2xl border border-[#1E2530] bg-[#161B27] p-5">
            <div className="mb-3 flex items-center justify-between">
              <span className="text-[11px] font-semibold uppercase tracking-wider text-[#4A5568]">발언 상세</span>
              <button
                onClick={closeMobile}
                className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-[#8B9BB4] hover:bg-[#1E2530] hover:text-white cursor-pointer"
              >
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
    </div>
  );
}
