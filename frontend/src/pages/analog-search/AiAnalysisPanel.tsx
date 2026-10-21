// 등재 아날로그 — AI 질문형 분석 (현재 검색 결과 범위) · 오른쪽 슬라이드 패널
// 정책 발언의 TimelineDrawer 와 같은 방식: 화면 오른쪽에 고정, 스크림 없음, 0.25초 슬라이드, ESC·닫기로 닫음.
// 닫아도 컴포넌트는 유지되어 진행 중인 분석·결과가 사라지지 않는다.
// 질문 → 본문에서 표현 찾기 → 분류·인용(원문 검증) → 제품별 근거 표 + 엑셀
import { useEffect, useRef, useState } from 'react';
import {
  startAnalogAiAnalysis, fetchAnalogAiJob, fetchAnalogAiLimits, downloadAnalogAiExcel,
  REVIEW_RESULT_KO, type AnalogAiJob, type AnalogAiLimits, type AnalogAiResult, type AnalogAiStrength,
} from '../../api/analog';

const EXAMPLES = [
  '계열 효과(class effect)로 볼 수 있는 표현이 있는 보고서는?',
  '동일 기전을 근거로 대체약제를 선정한 사례는?',
  '비용효과성 근거로 해외 급여 사례를 인용한 보고서는?',
];
const CAT_COLORS = ['bg-teal-100 text-teal-700', 'bg-blue-100 text-blue-700', 'bg-violet-100 text-violet-700', 'bg-amber-100 text-amber-700', 'bg-rose-100 text-rose-700'];
const STRENGTH_KO: Record<AnalogAiStrength, string> = { strong: '강', moderate: '중', weak: '약' };
const STRENGTH_STYLE: Record<AnalogAiStrength, string> = {
  strong: 'bg-emerald-100 text-emerald-700', moderate: 'bg-sky-100 text-sky-700', weak: 'bg-gray-100 text-gray-500',
};
const STRENGTH_HELP = '강: 직접 말함 · 중: 같은 취지 · 약: 단어만 있거나 간접적(AI 판정, 사람이 확인 필요)';
const STAGE_LABEL: Record<string, string> = { plan: '질문을 분석하는 중', candidates: '후보 문단을 찾는 중', judge: 'AI가 문단을 판정하는 중' };

export type AiStatus = 'idle' | 'running' | 'done';

interface Props {
  open: boolean;
  onClose: () => void;
  reportIds: number[];
  onOpen: (id: number) => void;
  onStatusChange?: (s: AiStatus) => void;
}

export default function AiAnalysisPanel({ open, onClose, reportIds, onOpen, onStatusChange }: Props) {
  const [question, setQuestion] = useState('');
  const [limits, setLimits] = useState<AnalogAiLimits | null>(null);
  const [job, setJob] = useState<AnalogAiJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showTerms, setShowTerms] = useState(false);
  const [showWeak, setShowWeak] = useState(false);
  const timer = useRef<number | null>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  const stop = () => { if (timer.current) { window.clearInterval(timer.current); timer.current = null; } };
  useEffect(() => stop, []);
  useEffect(() => {
    onStatusChange?.(busy || job?.status === 'running' ? 'running' : job?.status === 'done' ? 'done' : 'idle');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busy, job?.status]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    const t = window.setTimeout(() => (job ? closeRef : textRef).current?.focus(), 260);
    return () => { window.removeEventListener('keydown', onKey); window.clearTimeout(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, onClose]);
  useEffect(() => {
    if (!open) return;
    fetchAnalogAiLimits().then(setLimits).catch(() => setLimits(null));
  }, [open, job?.status]);

  const poll = (id: string) => {
    stop();
    const tick = async () => {
      try {
        const j = await fetchAnalogAiJob(id);
        setJob(j);
        if (j.status !== 'running') { stop(); setBusy(false); }
      } catch (e) { stop(); setBusy(false); setError(e instanceof Error ? e.message : String(e)); }
    };
    void tick();
    timer.current = window.setInterval(tick, 2500);
  };

  const max = limits?.max_reports ?? 150;
  const tooBig = reportIds.length > max;
  const noLeft = limits !== null && limits.remaining <= 0;
  const canRun = !busy && question.trim().length > 0 && reportIds.length > 0 && !tooBig && !noLeft;

  const run = async () => {
    setError(null); setJob(null); setBusy(true); setShowTerms(false); setShowWeak(false);
    try {
      const r = await startAnalogAiAnalysis(question.trim(), reportIds);
      poll(r.job_id);
    } catch (e) { setBusy(false); setError(e instanceof Error ? e.message : String(e)); }
  };

  const result: AnalogAiResult | undefined = job?.status === 'done' ? job.result : undefined;
  const catIndex = (key: string) => Math.max(0, (result?.by_category ?? []).findIndex(c => c.key === key));
  const visibleHits = (result?.hits ?? [])
    .map(h => ({ ...h, items: showWeak ? h.items : h.items.filter(it => it.strength !== 'weak') }))
    .filter(h => h.items.length > 0);
  const weakOnlyReports = (result?.hits.length ?? 0) - (result?.hits ?? []).filter(h => h.items.some(it => it.strength !== 'weak')).length;
  const pct = job?.progress && job.progress.total ? Math.round((job.progress.done / job.progress.total) * 100) : 0;

  return (
    <aside
      aria-label="AI 분석" aria-hidden={!open}
      style={{ visibility: open ? 'visible' : 'hidden', transition: open ? 'transform .25s' : 'transform .25s, visibility 0s linear .25s' }}
      className={`fixed top-0 right-0 h-screen w-[560px] max-w-[94vw] bg-white shadow-[-10px_0_30px_rgba(0,0,0,0.12)] z-40 overflow-y-auto ${open ? 'translate-x-0' : 'translate-x-full'}`}>
      <div className="sticky top-0 bg-white/95 backdrop-blur border-b border-gray-100 px-6 py-4 flex items-center justify-between z-10">
        <div>
          <h2 className="flex items-center gap-2 text-base font-bold text-teal-700"><i className="ri-sparkling-2-line"></i>AI 분석</h2>
          <p className="text-[11px] text-gray-500 mt-0.5">현재 검색 결과 {reportIds.length}건에 질문합니다</p>
        </div>
        <button ref={closeRef} type="button" onClick={onClose} tabIndex={open ? 0 : -1}
          className="text-xs bg-gray-100 hover:bg-gray-200 rounded-md px-3 py-1.5">닫기 ×</button>
      </div>

      {(
        <div className="px-6 py-5 space-y-4">
          <div>
            <textarea ref={textRef} value={question} onChange={e => setQuestion(e.target.value.slice(0, 300))} rows={2}
              placeholder="예: 계열 효과(class effect)로 볼 수 있는 표현이 있는 보고서는?"
              className="w-full text-sm border border-gray-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-teal-300" />
            <div className="flex flex-wrap items-center gap-2 mt-2">
              {EXAMPLES.map(ex => (
                <button key={ex} type="button" onClick={() => setQuestion(ex)}
                  className="text-[11px] text-gray-500 bg-gray-50 border border-gray-200 rounded-full px-3 py-1 hover:bg-teal-50 hover:text-teal-700">{ex}</button>
              ))}
              <span className="ml-auto text-[10px] text-gray-400">{question.length}/300</span>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3 text-xs">
            <button type="button" onClick={run} disabled={!canRun}
              className="flex items-center gap-1.5 bg-teal-600 text-white font-bold px-4 py-2 rounded-lg hover:bg-teal-700 disabled:opacity-40 disabled:cursor-not-allowed">
              <i className={busy ? 'ri-loader-4-line animate-spin' : 'ri-play-line'}></i>
              {busy ? '분석 중…' : 'AI로 분석'}
            </button>
            <span className="text-gray-500">분석 범위: 현재 검색 결과 <b>{reportIds.length}</b>건</span>
            {limits && <span className={noLeft ? 'text-red-500' : 'text-gray-400'}>오늘 남은 횟수 {limits.remaining}/{limits.limit}</span>}
            {tooBig && <span className="text-red-500">범위가 {max}건을 넘습니다. 필터로 줄여 주세요.</span>}
            {reportIds.length === 0 && <span className="text-gray-400">먼저 검색하거나 필터를 골라 주세요.</span>}
          </div>
          <p className="text-[11px] text-gray-400">
            본문에서 질문과 관련된 표현이 든 문단만 골라 AI가 판정합니다(보통 1~3분). 인용은 원문에 실제 있는 문장만 남깁니다.
          </p>

          {error && <p className="text-sm text-red-500 bg-red-50 rounded-lg px-3 py-2">{error}</p>}

          {job?.status === 'running' && (
            <div>
              <div className="flex justify-between text-xs text-gray-500 mb-1">
                <span>{STAGE_LABEL[job.progress?.stage ?? 'plan']}{job.progress?.stage === 'judge' ? ` (${job.progress.done}/${job.progress.total})` : ''}</span>
                <span>{pct}%</span>
              </div>
              <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden"><div className="h-full bg-teal-500 transition-all" style={{ width: `${Math.max(pct, 4)}%` }}></div></div>
            </div>
          )}
          {job?.status === 'failed' && (
            <p className="text-sm text-red-500 bg-red-50 rounded-lg px-3 py-2">분석 실패: {job.error ?? '알 수 없는 오류'}</p>
          )}

          {result && job && (
            <div className="space-y-4">
              <div className="rounded-xl bg-teal-50/60 border border-teal-100 p-4">
                <p className="text-sm font-bold text-teal-800">{result.interpretation || result.question}</p>
                <p className="text-xs text-gray-600 mt-1">
                  범위 {result.scope.reports}건 중 후보 문단이 있는 보고서 {result.scope.reports_with_candidates}건 · 판정한 문단 {result.scope.passages_judged}개
                  · <b className="text-teal-700">근거가 확인된 보고서 {result.hits.length}건</b> (<span title={STRENGTH_HELP}>강 {result.strength_reports.strong} · 중 {result.strength_reports.moderate} · 약 {result.strength_reports.weak}</span>)
                  {result.dropped_unverified > 0 && <span className="text-gray-400"> · 원문 검증에서 버린 인용 {result.dropped_unverified}건</span>}
                </p>
                <button type="button" onClick={() => setShowTerms(v => !v)} className="text-[11px] text-gray-500 underline decoration-dotted mt-1">
                  {showTerms ? '찾은 표현 숨기기' : `찾은 표현 ${result.terms.length + (result.stems?.length ?? 0)}개 보기`}
                </button>
                {showTerms && (
                  <div className="flex flex-wrap gap-1.5 mt-2">{result.terms.map(t => <span key={t} className="text-[11px] bg-white border border-gray-200 rounded-full px-2 py-0.5 text-gray-600">{t}</span>)}{(result.stems ?? []).map(t => <span key={'s' + t} title="후보를 넓게 잡는 어근" className="text-[11px] bg-teal-50 border border-teal-200 rounded-full px-2 py-0.5 text-teal-700">{t} (어근)</span>)}</div>
                )}
              </div>

              <div className="flex flex-wrap items-center gap-2">
                {result.by_category.map((c, i) => (
                  <span key={c.key} title={c.definition} className={`text-xs font-bold rounded-full px-3 py-1 ${CAT_COLORS[i % CAT_COLORS.length]}`}>
                    {c.label} · 보고서 {c.reports}건 / 인용 {c.quotes}건
                  </span>
                ))}
                {result.strength_counts.weak > 0 && (
                  <button type="button" onClick={() => setShowWeak(v => !v)} title={STRENGTH_HELP}
                    className="text-xs text-gray-500 underline decoration-dotted hover:text-gray-700">
                    {showWeak ? '약한 근거 숨기기' : `약한 근거 ${result.strength_counts.weak}건 보기`}
                  </button>
                )}
                <button type="button" onClick={() => downloadAnalogAiExcel(job.job_id).catch(e => setError(e instanceof Error ? e.message : String(e)))}
                  className="ml-auto flex items-center gap-1.5 text-xs font-bold text-teal-700 border border-teal-300 rounded-lg px-3 py-1.5 hover:bg-teal-50">
                  <i className="ri-file-excel-2-line"></i> 엑셀 받기
                </button>
              </div>

              {visibleHits.length === 0 ? (
                <p className="text-sm text-gray-500 bg-gray-50 rounded-lg px-4 py-3">{result.hits.length === 0 ? '이 범위에서 질문에 해당하는 근거 문장을 찾지 못했습니다. 위 "찾은 표현"에 없는 다른 말로 쓰였을 수 있습니다.' : `강·중 근거는 없고 약한 근거 ${result.strength_counts.weak}건만 있습니다. 위 "약한 근거 보기"로 확인할 수 있습니다.`}</p>
              ) : (
                <div className="divide-y divide-gray-100 border border-gray-100 rounded-xl">
                  {visibleHits.map(h => (
                    <div key={h.report_id} className="p-4">
                      <div className="flex flex-wrap items-center gap-2 mb-2">
                        <button type="button" onClick={() => onOpen(h.report_id)} className="text-sm font-bold text-gray-800 hover:text-teal-700 hover:underline">{h.brand_name ?? '(약제명 없음)'}</button>
                        <span className="text-xs text-gray-400">{h.session_date ?? '약평위일 미상'}</span>
                        {h.review_result && <span className="text-[11px] bg-gray-100 text-gray-600 rounded px-2 py-0.5">{REVIEW_RESULT_KO[h.review_result] ?? h.review_result}</span>}
                        {h.post_url && <a href={h.post_url} target="_blank" rel="noreferrer" className="ml-auto text-[11px] text-teal-600 hover:underline">출처 게시물 <i className="ri-external-link-line"></i></a>}
                      </div>
                      <div className="space-y-2">
                        {h.items.map((it, k) => (
                          <div key={k} className="text-xs">
                            <span className={`inline-block font-bold rounded px-2 py-0.5 mr-1 ${STRENGTH_STYLE[it.strength]}`} title={STRENGTH_HELP}>근거 {STRENGTH_KO[it.strength]}</span>
                            <span className={`inline-block font-bold rounded px-2 py-0.5 mr-2 ${CAT_COLORS[catIndex(it.category) % CAT_COLORS.length]}`}>{result.category_labels[it.category] ?? it.category}</span>
                            <span className="text-gray-400">{it.reason}</span>
                            <blockquote className="mt-1 border-l-2 border-gray-300 pl-3 text-gray-700 leading-relaxed">“{it.quote}”</blockquote>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              <ul className="text-[11px] text-gray-400 list-disc pl-4 space-y-0.5">
                {result.limitations.map(l => <li key={l}>{l}</li>)}
              </ul>
            </div>
          )}
        </div>
      )}
    </aside>
  );
}
