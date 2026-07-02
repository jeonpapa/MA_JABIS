import { useEffect, useMemo, useState } from 'react';
import PolicyEventModal from '@/components/policy/EventModal';
import CommitteeWorkspacePanel from '@/components/policy/CommitteeWorkspace';
import {
  downloadPolicyReportArtifact,
  fetchPolicyEvents,
  fetchPolicyOverview,
  PolicyChangeRecord,
  PolicyEvent,
  PolicyOverview,
  PolicyReportArtifact,
  PolicySearchResult,
  PolicyTopicLedger,
  searchPolicy,
} from '@/api/policyIntelligence';

const laneChip: Record<string, { label: string; tone: string }> = {
  monthly: { label: 'Monthly', tone: 'bg-indigo-50 text-indigo-700' },
  tf: { label: 'TF', tone: 'bg-purple-50 text-purple-700' },
  policy: { label: '정책', tone: 'bg-teal-50 text-teal-700' },
};

// 룰 엔진(대문자) + Hermes 큐레이션(소문자, "low" 포함) 모두 색상 매핑 — 대소문자 무관
function severityTone(sev?: string): string {
  const map: Record<string, string> = {
    'very high': 'bg-red-50 text-red-700 border-red-200',
    high: 'bg-orange-50 text-orange-700 border-orange-200',
    'medium-high': 'bg-amber-50 text-amber-700 border-amber-200',
    medium: 'bg-blue-50 text-blue-700 border-blue-200',
    low: 'bg-slate-100 text-slate-600 border-slate-200',
  };
  return map[(sev || '').toLowerCase()] || 'bg-gray-50 text-gray-700 border-gray-200';
}

function formatDate(value: string | null | undefined): string {
  if (!value) return '-';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value.slice(0, 10);
  return d.toLocaleDateString('ko-KR', { year: 'numeric', month: '2-digit', day: '2-digit' });
}

function SeverityBadge({ value }: { value: string }) {
  return (
    <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-semibold ${severityTone(value)}`}>
      {value}
    </span>
  );
}

function changeTypeLabel(t: string | undefined): { label: string; tone: string } {
  if (t === 'new_topic') return { label: '신규', tone: 'bg-emerald-50 text-emerald-700 border-emerald-200' };
  return { label: '업데이트', tone: 'bg-teal-50 text-teal-700 border-teal-200' };
}

// ── Level 1: 주제 카드 ────────────────────────────────────────────────────────
function TopicCard({ ledger, updates, onOpen }: { ledger: PolicyTopicLedger; updates: number; onOpen: () => void }) {
  return (
    <button
      onClick={onOpen}
      className="group flex h-full flex-col rounded-2xl border border-gray-200 bg-white p-5 text-left shadow-sm transition hover:border-teal-300 hover:shadow-md"
    >
      <div className="flex items-start justify-between gap-3">
        <h3 className="flex items-center gap-1.5 text-base font-bold leading-6 text-gray-950">
          {ledger.topic_name}
          {ledger.curation_source === 'hermes' && (
            <span className="inline-block h-2 w-2 rounded-full bg-emerald-500" title="AI 큐레이션" />
          )}
        </h3>
        <SeverityBadge value={ledger.severity} />
      </div>
      <p className="mt-2 line-clamp-2 text-sm leading-6 text-gray-600">{ledger.current_summary || '최신 요약 대기'}</p>
      {ledger.latest_change && (
        <div className="mt-3 rounded-xl bg-gray-50 p-3">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">최신 업데이트 · {formatDate(ledger.latest_change.date)}</p>
          <p className="mt-1 line-clamp-2 text-sm text-gray-700">{ledger.latest_change.after}</p>
        </div>
      )}
      <div className="mt-auto flex items-center justify-between gap-2 pt-4 text-xs text-gray-500">
        <span className="inline-flex items-center gap-1"><i className="ri-history-line" /> {updates}건 누적</span>
        <span>{formatDate(ledger.first_seen_at)} ~ {formatDate(ledger.latest_seen_at)}</span>
        {ledger.impact_assessment_ready && (
          <span className="rounded-full bg-red-50 px-2 py-0.5 font-bold text-red-700">Impact 준비</span>
        )}
      </div>
      <span className="mt-3 inline-flex items-center gap-1 text-sm font-semibold text-teal-700 group-hover:gap-2 transition-all">
        히스토리 보기 <i className="ri-arrow-right-line" />
      </span>
    </button>
  );
}

// ── Level 2: 주제 상세 (누적 현재상태 + 히스토리) ────────────────────────────
function TopicDetail({
  ledger, events, changeByEvent, artifacts, downloadingId, onDownloadArtifact, onOpenEvent, onBack,
}: {
  ledger: PolicyTopicLedger;
  events: PolicyEvent[];
  changeByEvent: Record<string, PolicyChangeRecord>;
  artifacts: PolicyReportArtifact[];
  downloadingId: string | null;
  onDownloadArtifact: (a: PolicyReportArtifact) => void;
  onOpenEvent: (id: string) => void;
  onBack: () => void;
}) {
  return (
    <div className="space-y-6">
      <button onClick={onBack} className="inline-flex items-center gap-1.5 text-sm font-semibold text-gray-600 hover:text-teal-700">
        <i className="ri-arrow-left-line" /> 전체 주제
      </button>

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-2xl font-black tracking-tight text-gray-950">{ledger.topic_name}</h2>
            <SeverityBadge value={ledger.severity} />
            {ledger.curation_source === 'hermes' && (
              <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-semibold text-emerald-700">
                <span className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-500" /> AI 큐레이션
              </span>
            )}
          </div>
          <p className="mt-1 text-sm text-gray-500">
            {formatDate(ledger.first_seen_at)} ~ {formatDate(ledger.latest_seen_at)} · {events.length}건 누적 · {ledger.current_status}
          </p>
        </div>
      </div>

      {/* 누적 현재 상태 — 최신 업데이트 중심 */}
      <section className="rounded-2xl border border-teal-200 bg-teal-50/60 p-5">
        <p className="text-xs font-semibold uppercase tracking-wide text-teal-700">현재 누적 상태 (최신 기준)</p>
        {ledger.latest_change && (
          <div className="mt-3 rounded-xl border border-teal-200 bg-white p-4">
            <p className="text-xs font-bold text-teal-700">최신 변화 · {formatDate(ledger.latest_change.date)}</p>
            <p className="mt-1 text-sm leading-6 text-gray-800">{ledger.latest_change.after}</p>
          </div>
        )}
        <p className="mt-3 text-sm leading-6 text-gray-800">{ledger.current_summary}</p>
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <div className="rounded-xl bg-white p-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">MSD 시사점</p>
            <p className="mt-1 text-sm text-gray-800">{ledger.msd_implication_latest.rationale}</p>
          </div>
          <div className="rounded-xl bg-white p-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">다음 액션</p>
            <p className="mt-1 text-sm font-medium text-teal-800">{ledger.msd_implication_latest.next_action}</p>
          </div>
        </div>
        {ledger.data_gaps?.length > 0 && (
          <p className="mt-3 text-xs text-gray-600"><i className="ri-error-warning-line mr-1 text-amber-600" />{ledger.data_gaps.join(' · ')}</p>
        )}
        {artifacts.length > 0 && (
          <div className="mt-4 flex flex-wrap gap-2">
            {artifacts.map(a => (
              <button
                key={a.id}
                onClick={() => onDownloadArtifact(a)}
                disabled={!a.available || downloadingId === a.id}
                className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-semibold text-gray-700 hover:border-teal-300 disabled:opacity-50"
              >
                <i className="ri-download-2-line" /> {a.title} ({a.format.toUpperCase()})
              </button>
            ))}
          </div>
        )}
      </section>

      {/* 히스토리 타임라인 (최신순) */}
      <section>
        <p className="mb-3 text-sm font-bold text-gray-950">히스토리 <span className="font-normal text-gray-500">· 최신순 · 클릭 시 메일 원문/첨부</span></p>
        <ol className="relative space-y-3 border-l-2 border-gray-100 pl-5">
          {events.map((ev, i) => {
            const chg = changeByEvent[ev.id];
            const ct = changeTypeLabel(chg?.change_type);
            return (
              <li key={ev.id} className="relative">
                <span className={`absolute -left-[27px] top-1.5 h-3 w-3 rounded-full border-2 ${i === 0 ? 'border-teal-500 bg-teal-500' : 'border-gray-300 bg-white'}`} />
                <button
                  onClick={() => onOpenEvent(ev.id)}
                  className="w-full rounded-xl border border-gray-200 bg-white p-4 text-left transition hover:border-teal-300 hover:shadow-sm"
                >
                  <div className="flex items-center justify-between gap-3">
                    <span className="flex items-center gap-1.5 text-xs font-medium text-gray-500">
                      {formatDate(ev.date)}
                      {ev.curation_source === 'hermes' && (
                        <span className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-500" title="AI 큐레이션" />
                      )}
                    </span>
                    <span className={`rounded-full border px-2 py-0.5 text-[11px] font-bold ${ct.tone}`}>{ct.label}</span>
                  </div>
                  <p className="mt-1.5 text-sm font-semibold leading-6 text-gray-900">{ev.subject}</p>
                  {chg?.why_it_matters && <p className="mt-1 line-clamp-2 text-xs text-gray-500">{chg.why_it_matters}</p>}
                  <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-500">
                    {ev.agencies.length > 0 && <span><i className="ri-government-line mr-1" />{ev.agencies.join(', ')}</span>}
                    <span><i className="ri-attachment-2 mr-1" />첨부 {ev.document_count}</span>
                    {ev.deadline && <span className="text-amber-700"><i className="ri-time-line mr-1" />{ev.deadline}</span>}
                    <span className="ml-auto font-semibold text-teal-700">메일·첨부 보기 →</span>
                  </div>
                </button>
              </li>
            );
          })}
          {events.length === 0 && <li className="text-sm text-gray-500">히스토리가 없습니다.</li>}
        </ol>
      </section>
    </div>
  );
}

export default function PolicyIntelligencePage() {
  const [overview, setOverview] = useState<PolicyOverview | null>(null);
  const [topicLedgers, setTopicLedgers] = useState<PolicyTopicLedger[]>([]);
  const [changeRecords, setChangeRecords] = useState<PolicyChangeRecord[]>([]);
  const [reportArtifacts, setReportArtifacts] = useState<PolicyReportArtifact[]>([]);
  const [events, setEvents] = useState<PolicyEvent[]>([]);
  const [selectedTopicId, setSelectedTopicId] = useState<string | null>(null);
  const [openEventId, setOpenEventId] = useState<string | null>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [subTab, setSubTab] = useState<'topics' | 'committee'>('topics');
  const [query, setQuery] = useState('');
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<PolicySearchResult[] | null>(null);

  useEffect(() => {
    let mounted = true;
    async function load() {
      setLoading(true); setError('');
      try {
        const [ov, evs] = await Promise.all([fetchPolicyOverview(), fetchPolicyEvents()]);
        if (!mounted) return;
        setOverview(ov.overview);
        setTopicLedgers(ov.topic_ledgers || []);
        setChangeRecords(ov.change_records || []);
        setReportArtifacts(ov.report_artifacts || []);
        setEvents(evs.items);
      } catch (e) {
        if (mounted) setError(e instanceof Error ? e.message : 'Policy Intelligence 데이터를 불러오지 못했습니다.');
      } finally {
        if (mounted) setLoading(false);
      }
    }
    void load();
    return () => { mounted = false; };
  }, []);

  const changeByEvent = useMemo(() => {
    const m: Record<string, PolicyChangeRecord> = {};
    for (const c of changeRecords) if (c.event_id) m[c.event_id] = c;
    return m;
  }, [changeRecords]);

  const selectedLedger = useMemo(
    () => topicLedgers.find(l => l.topic_id === selectedTopicId) || null,
    [topicLedgers, selectedTopicId],
  );

  const topicEvents = useMemo(
    () => selectedLedger ? events.filter(e => e.topic === selectedLedger.topic_name) : [],
    [events, selectedLedger],
  );

  const topicArtifacts = useMemo(
    () => selectedLedger ? reportArtifacts.filter(a => a.topic === selectedLedger.topic_name) : [],
    [reportArtifacts, selectedLedger],
  );

  const updatesByTopic = useMemo(() => {
    const m: Record<string, number> = {};
    for (const e of events) m[e.topic] = (m[e.topic] || 0) + 1;
    return m;
  }, [events]);

  const handleDownloadArtifact = async (a: PolicyReportArtifact) => {
    setDownloadingId(a.id); setError('');
    try { await downloadPolicyReportArtifact(a); }
    catch (e) { setError(e instanceof Error ? e.message : '다운로드 실패'); }
    finally { setDownloadingId(null); }
  };

  const runSearch = async (term: string) => {
    const t = term.trim();
    if (!t) { setResults(null); return; }
    setSearching(true); setError('');
    try {
      const r = await searchPolicy(t);
      setResults(r.results);
    } catch (e) {
      setError(e instanceof Error ? e.message : '검색 실패');
    } finally {
      setSearching(false);
    }
  };

  const resetToTopics = () => {
    setQuery(''); setResults(null); setSubTab('topics'); setSelectedTopicId(null);
  };

  return (
    <div className="min-h-screen bg-gray-50 text-gray-950">
      <div className="border-b border-gray-200 bg-white px-8 py-6">
        <div className="flex items-center gap-2 text-teal-700">
          <i className="ri-government-line text-xl" />
          <span className="text-sm font-bold uppercase tracking-[0.18em]">KRPIA / 정부 의견조회 / TF Tracker</span>
        </div>
        <h1 className="mt-1 text-3xl font-black tracking-tight">Policy Intelligence Hub</h1>
        {overview && (
          <div className="mt-2 flex flex-wrap gap-2 text-xs text-gray-500">
            <span className="rounded-full bg-gray-100 px-3 py-1">주제 {overview.topic_count}</span>
            <span className="rounded-full bg-gray-100 px-3 py-1">누적 이벤트 {overview.event_count}</span>
            <span className="rounded-full bg-gray-100 px-3 py-1">위원회 분리 {overview.committee_event_count ?? 0}</span>
            <span className="rounded-full bg-emerald-50 px-3 py-1 text-emerald-700">큐레이션 {overview.curated_event_count ?? 0} / 미처리 {overview.pending_analysis_count ?? 0}</span>
            <span className="rounded-full bg-gray-100 px-3 py-1">최신 {formatDate(overview.latest_event_date)}</span>
          </div>
        )}

        {/* 최상단 전체 키워드 검색 */}
        <div className="mt-4 flex max-w-2xl items-center gap-2">
          <div className="relative flex-1">
            <i className="ri-search-line absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              value={query}
              onChange={e => setQuery(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') runSearch(query); }}
              placeholder="키워드 검색 (제목·첨부 원문 전체) — 예: RWE, 유연계약, 재평가"
              className="w-full rounded-xl border border-gray-200 bg-white py-2.5 pl-10 pr-3 text-sm focus:border-teal-400 focus:outline-none"
            />
          </div>
          <button onClick={() => runSearch(query)} className="rounded-xl bg-gray-900 px-4 py-2.5 text-sm font-semibold text-white hover:bg-gray-800">검색</button>
          {results !== null && (
            <button onClick={resetToTopics} className="rounded-xl border border-gray-200 px-3 py-2.5 text-sm text-gray-600 hover:bg-gray-50">초기화</button>
          )}
        </div>

        {/* 서브탭 (검색 중이 아닐 때만) */}
        {results === null && (
          <div className="mt-4 flex gap-1 border-b border-gray-200">
            {([['topics', '주요 주제'], ['committee', 'KRPIA Committee']] as const).map(([key, label]) => (
              <button
                key={key}
                onClick={() => { setSubTab(key); setSelectedTopicId(null); }}
                className={`-mb-px border-b-2 px-4 py-2 text-sm font-semibold transition ${subTab === key ? 'border-teal-600 text-teal-700' : 'border-transparent text-gray-500 hover:text-gray-800'}`}
              >
                {label}
              </button>
            ))}
          </div>
        )}
      </div>

      <main className="px-8 py-7">
        {error && <div className="mb-4 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</div>}
        {loading && <div className="rounded-xl border border-gray-200 bg-white p-6 text-gray-600">데이터를 불러오는 중입니다...</div>}

        {/* 검색 결과 (서브탭 무관, 최상단 검색 시) */}
        {results !== null && (
          <section>
            <h2 className="mb-3 text-lg font-bold">검색 결과 <span className="text-sm font-normal text-gray-500">· {searching ? '검색 중...' : `${results.length}건`}</span></h2>
            <div className="space-y-2">
              {results.map((r, i) => {
                const chip = laneChip[r.lane] || laneChip.policy;
                return (
                  <button
                    key={`${r.event_id}-${r.doc_id || 'e'}-${i}`}
                    onClick={() => setOpenEventId(r.event_id)}
                    className="w-full rounded-xl border border-gray-200 bg-white p-4 text-left transition hover:border-teal-300 hover:shadow-sm"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span className={`rounded-full px-2 py-0.5 text-[11px] font-bold ${chip.tone}`}>{r.tf_name || chip.label}</span>
                      <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-semibold text-gray-600">{r.type === 'document' ? '첨부' : '메일'}</span>
                      <span className="text-xs text-gray-400">{formatDate(r.date)}</span>
                      {r.topic && r.topic !== '기타' && <span className="text-xs text-gray-500">· {r.topic}</span>}
                    </div>
                    <p className="mt-1.5 text-sm font-semibold text-gray-900">{r.filename || r.subject}</p>
                    {r.snippet && <p className="mt-1 line-clamp-2 text-xs text-gray-500">{r.snippet}</p>}
                  </button>
                );
              })}
              {!searching && results.length === 0 && <p className="text-sm text-gray-500">일치하는 내용이 없습니다.</p>}
            </div>
          </section>
        )}

        {/* 주요 주제 서브탭 */}
        {!loading && results === null && subTab === 'topics' && !selectedLedger && (
          <section>
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {topicLedgers.map(ledger => (
                <TopicCard
                  key={ledger.topic_id}
                  ledger={ledger}
                  updates={updatesByTopic[ledger.topic_name] || ledger.events.length}
                  onOpen={() => setSelectedTopicId(ledger.topic_id)}
                />
              ))}
            </div>
            {topicLedgers.length === 0 && <p className="text-sm text-gray-500">표시할 주제가 없습니다.</p>}
          </section>
        )}

        {!loading && results === null && subTab === 'topics' && selectedLedger && (
          <TopicDetail
            ledger={selectedLedger}
            events={topicEvents}
            changeByEvent={changeByEvent}
            artifacts={topicArtifacts}
            downloadingId={downloadingId}
            onDownloadArtifact={handleDownloadArtifact}
            onOpenEvent={setOpenEventId}
            onBack={() => setSelectedTopicId(null)}
          />
        )}

        {/* KRPIA Committee 서브탭 */}
        {!loading && results === null && subTab === 'committee' && (
          <CommitteeWorkspacePanel onOpenEvent={setOpenEventId} />
        )}
      </main>

      {openEventId && <PolicyEventModal eventId={openEventId} onClose={() => setOpenEventId(null)} />}
    </div>
  );
}
