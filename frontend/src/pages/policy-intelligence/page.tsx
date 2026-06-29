import { useEffect, useMemo, useState } from 'react';
import {
  fetchPolicyDocuments,
  fetchPolicyEvents,
  fetchPolicyOverview,
  PolicyDocument,
  PolicyEvent,
  PolicyImpactCandidate,
  PolicyOverview,
  PolicyTopic,
} from '@/api/policyIntelligence';

const severityTone: Record<string, string> = {
  'Very High': 'bg-red-50 text-red-700 border-red-200',
  High: 'bg-orange-50 text-orange-700 border-orange-200',
  'Medium-High': 'bg-amber-50 text-amber-700 border-amber-200',
  Medium: 'bg-blue-50 text-blue-700 border-blue-200',
};

function formatDate(value: string | null | undefined): string {
  if (!value) return '-';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value.slice(0, 10);
  return d.toLocaleDateString('ko-KR', { year: 'numeric', month: '2-digit', day: '2-digit' });
}

function KpiCard({ label, value, hint, icon }: { label: string; value: number | string; hint: string; icon: string }) {
  return (
    <div className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-gray-500">{label}</p>
          <p className="mt-2 text-3xl font-bold text-gray-950">{value}</p>
          <p className="mt-2 text-xs text-gray-500">{hint}</p>
        </div>
        <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-teal-50 text-teal-700">
          <i className={`${icon} text-xl`} />
        </span>
      </div>
    </div>
  );
}

function SeverityBadge({ value }: { value: string }) {
  return (
    <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-semibold ${severityTone[value] || 'bg-gray-50 text-gray-700 border-gray-200'}`}>
      {value}
    </span>
  );
}

function ImpactCard({ item }: { item: PolicyImpactCandidate }) {
  return (
    <div className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="rounded-full bg-gray-900 px-2 py-0.5 text-xs font-bold text-white">P{item.priority}</span>
            <SeverityBadge value={item.severity} />
          </div>
          <h3 className="mt-3 text-base font-bold text-gray-950">{item.title}</h3>
          <p className="mt-1 text-sm font-medium text-teal-700">{item.topic}</p>
        </div>
        <span className="text-xs text-gray-500">{item.event_count} events</span>
      </div>
      <p className="mt-3 text-sm leading-6 text-gray-700">{item.rationale}</p>
      <div className="mt-4 rounded-xl bg-gray-50 p-3">
        <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">Next action</p>
        <p className="mt-1 text-sm text-gray-800">{item.next_action}</p>
      </div>
    </div>
  );
}

export default function PolicyIntelligencePage() {
  const [overview, setOverview] = useState<PolicyOverview | null>(null);
  const [topics, setTopics] = useState<PolicyTopic[]>([]);
  const [impacts, setImpacts] = useState<PolicyImpactCandidate[]>([]);
  const [events, setEvents] = useState<PolicyEvent[]>([]);
  const [documents, setDocuments] = useState<PolicyDocument[]>([]);
  const [selectedTopic, setSelectedTopic] = useState('All');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let mounted = true;
    async function load() {
      setLoading(true);
      setError('');
      try {
        const [overviewRes, eventsRes, docsRes] = await Promise.all([
          fetchPolicyOverview(),
          fetchPolicyEvents(),
          fetchPolicyDocuments(),
        ]);
        if (!mounted) return;
        setOverview(overviewRes.overview);
        setTopics(overviewRes.topics);
        setImpacts(overviewRes.impact_candidates);
        setEvents(eventsRes.items);
        setDocuments(docsRes.items);
      } catch (e) {
        if (mounted) setError(e instanceof Error ? e.message : 'Policy Intelligence 데이터를 불러오지 못했습니다.');
      } finally {
        if (mounted) setLoading(false);
      }
    }
    void load();
    return () => { mounted = false; };
  }, []);

  const filteredEvents = useMemo(
    () => selectedTopic === 'All' ? events : events.filter(e => e.topic === selectedTopic),
    [events, selectedTopic],
  );

  const filteredDocuments = useMemo(
    () => selectedTopic === 'All' ? documents : documents.filter(d => d.topic === selectedTopic),
    [documents, selectedTopic],
  );

  return (
    <div className="min-h-screen bg-gray-50 text-gray-950">
      <div className="border-b border-gray-200 bg-white px-8 py-6">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="mb-2 flex items-center gap-2 text-teal-700">
              <i className="ri-government-line text-xl" />
              <span className="text-sm font-bold uppercase tracking-[0.18em]">KRPIA / Government Consultation / TF Tracker</span>
            </div>
            <h1 className="text-3xl font-black tracking-tight">Policy Intelligence Hub</h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-gray-600">
              협회 TF, 복지부·심평원·공단 의견조회, 회사 전달 메일과 첨부 원문을 누적 관리하고 MSD implication 및 impact assessment 후보를 구조화합니다.
            </p>
          </div>
          <div className="rounded-2xl border border-teal-100 bg-teal-50 px-4 py-3 text-sm text-teal-800">
            <p className="font-semibold">Confidential working intelligence</p>
            <p className="mt-1 text-xs">원본 메일·첨부 파일은 private storage에 보존하고 대시보드에는 sanitized metadata만 노출합니다.</p>
          </div>
        </div>
      </div>

      <main className="space-y-6 px-8 py-7">
        {error && <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</div>}
        {loading && <div className="rounded-xl border border-gray-200 bg-white p-6 text-gray-600">데이터를 불러오는 중입니다...</div>}

        {!loading && overview && (
          <>
            <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
              <KpiCard label="Total events" value={overview.event_count} hint={`Latest ${formatDate(overview.latest_event_date)}`} icon="ri-timeline-view" />
              <KpiCard label="Topics" value={overview.topic_count} hint="주제별 latest-state summary" icon="ri-node-tree" />
              <KpiCard label="Documents" value={overview.document_count} hint="첨부·추출 텍스트 기준" icon="ri-file-text-line" />
              <KpiCard label="High impact" value={overview.high_impact_count} hint="High / Very High" icon="ri-alarm-warning-line" />
              <KpiCard label="Report" value={overview.report_available ? 'Ready' : 'Draft'} hint="Impact report candidate" icon="ri-file-chart-line" />
            </section>

            <section className="grid gap-6 xl:grid-cols-[1.05fr_0.95fr]">
              <div className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
                <div className="mb-4 flex items-center justify-between gap-3">
                  <div>
                    <h2 className="text-lg font-bold">Topic Tracker</h2>
                    <p className="text-sm text-gray-500">최신 커뮤니케이션 기준 주제별 상태와 다음 액션</p>
                  </div>
                  <select
                    value={selectedTopic}
                    onChange={e => setSelectedTopic(e.target.value)}
                    className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm"
                  >
                    <option value="All">All topics</option>
                    {topics.map(t => <option key={t.topic} value={t.topic}>{t.topic}</option>)}
                  </select>
                </div>
                <div className="space-y-3">
                  {topics.map(topic => (
                    <button
                      key={topic.topic}
                      onClick={() => setSelectedTopic(topic.topic)}
                      className={`w-full rounded-xl border p-4 text-left transition ${selectedTopic === topic.topic ? 'border-teal-300 bg-teal-50' : 'border-gray-200 hover:border-gray-300'}`}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="font-bold text-gray-950">{topic.topic}</p>
                          <p className="mt-1 text-sm text-gray-600">{topic.latest_subject}</p>
                        </div>
                        <SeverityBadge value={topic.severity} />
                      </div>
                      <div className="mt-3 grid gap-2 text-xs text-gray-500 sm:grid-cols-3">
                        <span>{topic.event_count} events</span>
                        <span>{formatDate(topic.latest_date)}</span>
                        <span>{topic.status}</span>
                      </div>
                      <p className="mt-3 text-sm text-gray-700">{topic.next_action}</p>
                    </button>
                  ))}
                </div>
              </div>

              <div className="space-y-4">
                <div>
                  <h2 className="text-lg font-bold">MSD Impact Candidates</h2>
                  <p className="text-sm text-gray-500">impact assessment 보고서로 확장할 우선순위 후보</p>
                </div>
                {impacts.map(item => <ImpactCard key={item.topic} item={item} />)}
              </div>
            </section>

            <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h2 className="text-lg font-bold">Timeline</h2>
                  <p className="text-sm text-gray-500">날짜별 커뮤니케이션 누적 히스토리</p>
                </div>
                <button onClick={() => setSelectedTopic('All')} className="text-sm font-semibold text-teal-700">Reset filter</button>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[980px] text-left text-sm">
                  <thead className="border-b border-gray-200 text-xs uppercase tracking-wide text-gray-500">
                    <tr>
                      <th className="py-3 pr-4">Date</th>
                      <th className="py-3 pr-4">Subject</th>
                      <th className="py-3 pr-4">Topic</th>
                      <th className="py-3 pr-4">Agency</th>
                      <th className="py-3 pr-4">Severity</th>
                      <th className="py-3 pr-4">Docs</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {filteredEvents.map(event => (
                      <tr key={event.id} className="align-top">
                        <td className="py-4 pr-4 font-medium text-gray-700">{formatDate(event.date)}</td>
                        <td className="py-4 pr-4">
                          <p className="font-semibold text-gray-950">{event.subject}</p>
                          <p className="mt-1 text-xs text-gray-500">{event.status}</p>
                        </td>
                        <td className="py-4 pr-4 text-gray-700">{event.topic}</td>
                        <td className="py-4 pr-4 text-gray-700">{event.agencies.join(', ') || '-'}</td>
                        <td className="py-4 pr-4"><SeverityBadge value={event.severity} /></td>
                        <td className="py-4 pr-4 text-gray-700">{event.document_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
              <h2 className="text-lg font-bold">Document Register</h2>
              <p className="mt-1 text-sm text-gray-500">원본은 private storage에 저장, 여기에는 추출 상태와 원본 식별 metadata만 표시</p>
              <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {filteredDocuments.map(doc => (
                  <div key={doc.id} className="rounded-xl border border-gray-200 p-4">
                    <div className="flex items-start gap-3">
                      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gray-100 text-gray-700">
                        <i className="ri-attachment-2" />
                      </span>
                      <div className="min-w-0">
                        <p className="truncate font-semibold text-gray-950" title={doc.filename}>{doc.filename}</p>
                        <p className="mt-1 text-xs text-gray-500">{doc.topic}</p>
                        <p className="mt-2 text-xs text-gray-600">{doc.char_count.toLocaleString()} chars · {doc.status} · {doc.text_available ? 'text extracted' : 'pending'}</p>
                      </div>
                    </div>
                  </div>
                ))}
                {filteredDocuments.length === 0 && <p className="text-sm text-gray-500">선택한 주제의 문서가 없습니다.</p>}
              </div>
            </section>
          </>
        )}
      </main>
    </div>
  );
}
