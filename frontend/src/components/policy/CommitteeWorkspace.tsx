import { useEffect, useMemo, useState } from 'react';
import {
  CommitteeMaterial,
  CommitteeTf,
  CommitteeWorkspace as CommitteeWorkspaceData,
  fetchCommitteeWorkspace,
  MonthlyMeeting,
} from '@/api/policyIntelligence';

const severityTone: Record<string, string> = {
  'Very High': 'bg-red-50 text-red-700 border-red-200',
  High: 'bg-orange-50 text-orange-700 border-orange-200',
  'Medium-High': 'bg-amber-50 text-amber-700 border-amber-200',
  Medium: 'bg-blue-50 text-blue-700 border-blue-200',
};

function fmt(v: string | null | undefined): string {
  if (!v) return '-';
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return v.slice(0, 10);
  return d.toLocaleDateString('ko-KR', { year: 'numeric', month: '2-digit', day: '2-digit' });
}

function monthLabel(m: string): string {
  if (!/^\d{4}-\d{2}$/.test(m)) return m;
  const [y, mm] = m.split('-');
  return `${y}년 ${Number(mm)}월`;
}

function DocChips({ material, onOpen }: { material: CommitteeMaterial | MonthlyMeeting; onOpen: () => void }) {
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2">
      {material.documents.map(d => (
        <span key={d.id} className="inline-flex max-w-full items-center gap-1 rounded-lg border border-gray-200 bg-white px-2 py-1 text-xs text-gray-600">
          <i className="ri-attachment-2 shrink-0" />
          <span className="truncate" title={d.filename || ''}>{d.filename}</span>
        </span>
      ))}
      <button onClick={onOpen} className="inline-flex items-center gap-1 rounded-lg bg-teal-50 px-2.5 py-1 text-xs font-semibold text-teal-700 hover:bg-teal-100">
        메일·첨부 원문 <i className="ri-arrow-right-line" />
      </button>
    </div>
  );
}

function MonthlyCard({ m, onOpen }: { m: MonthlyMeeting; onOpen: () => void }) {
  return (
    <div className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center gap-1 rounded-full bg-indigo-50 px-2.5 py-1 text-xs font-bold text-indigo-700">
            <i className="ri-calendar-event-line" /> {monthLabel(m.month)}
          </span>
          {m.meeting_no && <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs font-semibold text-gray-600">{m.meeting_no}차</span>}
        </div>
        <span className="text-xs text-gray-400">수신 {fmt(m.received_utc)}</span>
      </div>
      <p className="mt-2 text-sm font-semibold text-gray-900">{m.subject}</p>
      <DocChips material={m} onOpen={onOpen} />

      <div className="mt-4">
        <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">이 달 주로 다뤄진 주제 · MSD 시사점</p>
        {m.discussed_topics.length === 0 && <p className="mt-2 text-sm text-gray-500">첨부에서 식별된 주제가 없습니다.</p>}
        <div className="mt-2 space-y-2">
          {m.discussed_topics.map(t => (
            <div key={t.topic} className="rounded-xl border border-gray-100 bg-gray-50 p-3">
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm font-bold text-gray-900">{t.topic}</p>
                <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${severityTone[t.severity] || 'bg-gray-50 text-gray-700 border-gray-200'}`}>{t.severity}</span>
              </div>
              <p className="mt-1 text-xs leading-5 text-gray-600">{t.rationale}</p>
              <p className="mt-1 text-xs font-semibold text-teal-700">→ {t.next_action}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function TfColumn({ tf, onOpen }: { tf: CommitteeTf; onOpen: (id: string) => void }) {
  return (
    <div className="flex h-full flex-col rounded-2xl border border-gray-200 bg-white p-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-bold text-gray-950">{tf.name}</h3>
          <p className="mt-0.5 text-xs text-gray-500">{tf.description}</p>
        </div>
        <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-bold ${tf.material_count > 0 ? 'bg-purple-50 text-purple-700' : 'bg-gray-100 text-gray-400'}`}>
          {tf.material_count}
        </span>
      </div>
      <div className="mt-3 flex-1 space-y-2">
        {tf.materials.length === 0 && (
          <div className="rounded-xl border border-dashed border-gray-200 p-4 text-center text-xs text-gray-400">
            자료 추가 예정
          </div>
        )}
        {tf.materials.map(mat => (
          <button
            key={mat.event_id}
            onClick={() => onOpen(mat.event_id)}
            className="w-full rounded-xl border border-gray-200 p-3 text-left transition hover:border-purple-300 hover:shadow-sm"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-medium text-gray-500">{fmt(mat.received_utc)}</span>
              <span className="text-xs text-gray-400"><i className="ri-attachment-2 mr-0.5" />{mat.documents.length}</span>
            </div>
            <p className="mt-1 line-clamp-2 text-sm font-semibold text-gray-900">{mat.subject}</p>
            <span className="mt-1 inline-block text-xs font-semibold text-purple-700">메일·첨부 보기 →</span>
          </button>
        ))}
      </div>
    </div>
  );
}

/** Policy Intelligence 탭의 'KRPIA Committee' 서브탭 콘텐츠 (Monthly Meeting + 4 TF). */
export default function CommitteeWorkspacePanel({ onOpenEvent }: { onOpenEvent: (id: string) => void }) {
  const [ws, setWs] = useState<CommitteeWorkspaceData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let mounted = true;
    setLoading(true); setError('');
    fetchCommitteeWorkspace()
      .then(d => { if (mounted) setWs(d); })
      .catch(e => { if (mounted) setError(e instanceof Error ? e.message : '위원회 데이터를 불러오지 못했습니다.'); })
      .finally(() => { if (mounted) setLoading(false); });
    return () => { mounted = false; };
  }, []);

  const grouped = useMemo(() => {
    const g: Record<string, MonthlyMeeting[]> = {};
    for (const m of ws?.monthly_meetings || []) (g[m.month] ||= []).push(m);
    return Object.entries(g).sort((a, b) => b[0].localeCompare(a[0]));
  }, [ws]);

  if (loading) return <div className="rounded-xl border border-gray-200 bg-white p-6 text-gray-600">불러오는 중...</div>;
  if (error) return <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</div>;
  if (!ws) return null;

  return (
    <div className="space-y-9">
      {/* Monthly Meeting */}
      <section>
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h2 className="text-lg font-bold">Monthly Meeting</h2>
            <p className="text-sm text-gray-500">월례회의별 첨부 원문 + 그 달 주로 다뤄진 주제·시사점(첨부 자동 분석)</p>
          </div>
          <span className="rounded-full bg-gray-100 px-3 py-1 text-xs font-semibold text-gray-600">{ws.summary.monthly_count}회</span>
        </div>
        <div className="grid gap-4 xl:grid-cols-2">
          {grouped.map(([, list]) => list.map(m => (
            <MonthlyCard key={m.event_id} m={m} onOpen={() => onOpenEvent(m.event_id)} />
          )))}
        </div>
        {ws.monthly_meetings.length === 0 && <p className="text-sm text-gray-500">등록된 월례회의가 없습니다.</p>}
      </section>

      {/* TF Activities */}
      <section>
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h2 className="text-lg font-bold">TF 활동</h2>
            <p className="text-sm text-gray-500">TF별 자료는 계속 추가됩니다 · {ws.summary.tf_with_materials}/{ws.summary.tf_count} TF 자료 보유</p>
          </div>
        </div>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {ws.tfs.map(tf => <TfColumn key={tf.id} tf={tf} onOpen={onOpenEvent} />)}
        </div>
      </section>
    </div>
  );
}
