import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  cancelAnalysisRequest,
  createAnalysisRequest,
  downloadAnalysisFile,
  getAnalysisRequest,
  listAnalysisRequests,
  replyAnalysisRequest,
  sourceText,
  type AnalysisRequest,
} from '@/api/analysisRequests';
import { USER_KEY } from '@/api/client';

// 분석 요청 (A안, 2026-11-02): 대시보드 데이터로 계산·연결이 필요한 질문을 남기면 Aside 분석 담당이 처리해 결과를 올린다.
// 서버는 분석을 실행하지 않는다. 결과는 요약·방법·가정·데이터 기준일·출처·한계·첨부 파일로 구성된다.

const EXAMPLES = [
  '2022~2026 약평위에서 대체약제 가중평균가(WAP) 대비 90% 미만으로 등재된 아날로그를 찾아 주세요',
  '키트루다 A8 조정가가 최근 1년 동안 국가별로 어떻게 바뀌었는지 비교해 주세요',
  'DPP-4 억제제 성분별 2025년 분기 점유율 추이를 표로 정리해 주세요',
];

function tone(status: string): string {
  if (status === 'completed') return 'bg-[#00E5CC]/10 text-[#00E5CC]';
  if (status === 'failed') return 'bg-red-500/10 text-red-400';
  if (status === 'needs_info') return 'bg-[#F59E0B]/15 text-[#F59E0B]';
  if (status === 'processing') return 'bg-[#3B82F6]/10 text-[#60A5FA]';
  if (status === 'cancelled') return 'bg-[#1E2530] text-[#8B9BB4]';
  return 'bg-[#1E2530] text-[#C9D3E3]';
}

function fmt(s?: string | null): string {
  if (!s) return '—';
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? s : d.toLocaleString('ko-KR');
}

function kb(n: number): string {
  return n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)}MB` : `${Math.max(1, Math.round(n / 1024))}KB`;
}

function currentEmail(): string {
  // utils/authUsers.ts 가 로그인 이메일 문자열을 그대로 저장한다
  return (localStorage.getItem(USER_KEY) || '').trim();
}

export default function AnalysisRequestsPage() {
  const [items, setItems] = useState<AnalysisRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [question, setQuestion] = useState('');
  const [shareTeam, setShareTeam] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [formMsg, setFormMsg] = useState<string | null>(null);
  const [selected, setSelected] = useState<AnalysisRequest | null>(null);
  const [reply, setReply] = useState('');
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState<'mine' | 'team'>('mine');
  const me = useMemo(currentEmail, []);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setItems(await listAnalysisRequests());
    } catch (e) {
      setError(e instanceof Error ? e.message : '목록 조회 실패');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const open = async (id: string) => {
    try {
      setSelected(await getAnalysisRequest(id));
      setReply('');
    } catch (e) {
      setError(e instanceof Error ? e.message : '상세 조회 실패');
    }
  };

  const submit = async () => {
    setSubmitting(true);
    setFormMsg(null);
    try {
      const r = await createAnalysisRequest(question.trim(), shareTeam);
      setQuestion('');
      setFormMsg('접수했습니다. 처리 상태는 아래 목록에서 확인하세요.');
      await reload();
      setSelected(r);
    } catch (e) {
      setFormMsg(e instanceof Error ? e.message : '요청 실패');
    } finally {
      setSubmitting(false);
    }
  };

  const act = async (fn: () => Promise<AnalysisRequest>) => {
    setBusy(true);
    try {
      setSelected(await fn());
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : '처리 실패');
    } finally {
      setBusy(false);
    }
  };

  const shown = items.filter(r => (filter === 'mine' ? r.requester === me || !me : r.status === 'completed'));
  const isMine = selected && (selected.requester === me || !me);

  return (
    <div className="min-h-screen bg-[#0D1117] text-white">
      <div className="px-8 pt-8 pb-6 border-b border-[#1E2530]">
        <div className="flex items-center gap-2 mb-1">
          <i className="ri-flask-line text-[#00E5CC]"></i>
          <h1 className="text-2xl font-bold text-white">분석 요청</h1>
        </div>
        <p className="text-[#8B9BB4] text-sm max-w-3xl leading-relaxed">
          대시보드의 데이터(약평위 아날로그·국내 약가·IQVIA 점유율·해외 약가 등)를 연결·계산해야 하는 질문을 남기면 분석 담당(Aside)이
          처리해 결과를 올립니다. 결과에는 요약·방법·가정·데이터 기준일·출처·한계와 첨부 파일이 함께 표시됩니다. 처리에는 보통 몇 시간이
          걸리며, 질문이 애매하면 되묻는 메시지가 올 수 있습니다.
        </p>
      </div>

      <div className="px-8 py-6 grid grid-cols-1 lg:grid-cols-5 gap-5 items-start">
        <div className="lg:col-span-2 space-y-5">
          <section className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5">
            <h2 className="text-white font-bold text-sm mb-3">새 분석 요청</h2>
            <textarea
              value={question}
              onChange={e => setQuestion(e.target.value)}
              rows={5}
              maxLength={2000}
              placeholder="무엇을 알고 싶은지, 대상 기간·제품·조건을 구체적으로 적어 주세요."
              className="w-full rounded-xl bg-[#0D1117] border border-[#1E2530] p-3 text-sm text-white placeholder-[#4A5568] focus:outline-none focus:border-[#00E5CC]/60"
            />
            <div className="mt-2 flex flex-wrap gap-2">
              {EXAMPLES.map(x => (
                <button key={x} type="button" onClick={() => setQuestion(x)} className="text-[11px] px-2 py-1 rounded-lg bg-[#1E2530] text-[#8B9BB4] hover:text-white text-left">
                  예: {x.length > 38 ? x.slice(0, 38) + '…' : x}
                </button>
              ))}
            </div>
            <label className="mt-3 flex items-center gap-2 text-xs text-[#C9D3E3]">
              <input type="checkbox" checked={shareTeam} onChange={e => setShareTeam(e.target.checked)} />
              완료 결과를 팀 전체에 공개 (해제하면 나와 관리자만 봄)
            </label>
            <div className="mt-3 flex items-center justify-between gap-3">
              <span className="text-[11px] text-[#4A5568]">1인 하루 3건 · 진행 중 3건까지 · {question.trim().length}/2000자</span>
              <button
                type="button"
                disabled={submitting || question.trim().length < 10}
                onClick={submit}
                className="px-4 py-2 rounded-xl bg-[#00E5CC] text-[#0D1117] text-sm font-semibold disabled:opacity-40"
              >
                {submitting ? '요청 중…' : '요청하기'}
              </button>
            </div>
            {formMsg && <p className="mt-2 text-xs text-[#8B9BB4]">{formMsg}</p>}
          </section>

          <section className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5">
            <div className="flex items-center justify-between mb-3">
              <div className="flex gap-2">
                {(['mine', 'team'] as const).map(f => (
                  <button key={f} type="button" onClick={() => setFilter(f)}
                    className={`text-xs px-3 py-1 rounded-lg ${filter === f ? 'bg-[#00E5CC]/15 text-[#00E5CC]' : 'text-[#8B9BB4] hover:text-white'}`}>
                    {f === 'mine' ? '내 요청' : '팀 공개 결과'}
                  </button>
                ))}
              </div>
              <button onClick={reload} className="text-[#8B9BB4] text-xs hover:text-white flex items-center gap-1">
                <i className="ri-refresh-line"></i>새로고침
              </button>
            </div>
            {loading && <p className="text-center py-8 text-sm text-[#8B9BB4]">불러오는 중…</p>}
            {!loading && error && <p className="text-center py-8 text-sm text-red-400">{error}</p>}
            {!loading && !error && shown.length === 0 && (
              <p className="text-center py-8 text-sm text-[#8B9BB4]">{filter === 'mine' ? '아직 요청이 없습니다.' : '공개된 결과가 없습니다.'}</p>
            )}
            <ul className="space-y-2">
              {shown.map(r => (
                <li key={r.id}>
                  <button type="button" onClick={() => open(r.id)}
                    className={`w-full text-left rounded-xl border p-3 ${selected?.id === r.id ? 'border-[#00E5CC]/50 bg-[#00E5CC]/5' : 'border-[#1E2530] hover:border-[#2A3545]'}`}>
                    <div className="flex items-center gap-2 mb-1">
                      <span className={`text-[11px] px-2 py-0.5 rounded-md ${tone(r.status)}`}>{r.status_label}</span>
                      <span className="text-[11px] text-[#4A5568]">{fmt(r.created_at)}</span>
                      {!r.share_team && <span className="text-[11px] text-[#4A5568]">· 나만 보기</span>}
                    </div>
                    <p className="text-sm text-white line-clamp-2">{r.question}</p>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        </div>

        <section className="lg:col-span-3 bg-[#161B27] rounded-2xl border border-[#1E2530] p-6 min-h-[320px]">
          {!selected && <p className="text-sm text-[#8B9BB4]">왼쪽 목록에서 요청을 선택하면 진행 상태와 결과가 보입니다.</p>}
          {selected && (
            <div className="space-y-5">
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <span className={`text-xs px-2 py-0.5 rounded-md ${tone(selected.status)}`}>{selected.status_label}</span>
                  <span className="text-xs text-[#4A5568]">요청 {fmt(selected.created_at)}{selected.completed_at ? ` · 완료 ${fmt(selected.completed_at)}` : ''}</span>
                </div>
                <p className="text-xs text-[#8B9BB4] mb-1">질문 · {selected.requester}</p>
                <p className="text-sm text-white whitespace-pre-wrap leading-relaxed">{selected.question}</p>
              </div>

              {selected.status === 'needs_info' && (
                <div className="rounded-xl border border-[#F59E0B]/40 bg-[#F59E0B]/5 p-4">
                  <p className="text-xs font-semibold text-[#F59E0B] mb-1">분석 담당의 확인 질문</p>
                  <p className="text-sm text-white whitespace-pre-wrap">{selected.clarification}</p>
                  {isMine && (
                    <div className="mt-3">
                      <textarea value={reply} onChange={e => setReply(e.target.value)} rows={3} maxLength={2000}
                        className="w-full rounded-xl bg-[#0D1117] border border-[#1E2530] p-3 text-sm text-white" placeholder="답변을 입력하면 다시 접수됩니다." />
                      <button type="button" disabled={busy || !reply.trim()} onClick={() => act(() => replyAnalysisRequest(selected.id, reply.trim()))}
                        className="mt-2 px-4 py-2 rounded-xl bg-[#F59E0B] text-[#0D1117] text-sm font-semibold disabled:opacity-40">답변 보내기</button>
                    </div>
                  )}
                </div>
              )}
              {selected.reply && selected.status !== 'needs_info' && (
                <p className="text-xs text-[#8B9BB4]">확인 질문: {selected.clarification} → 답변: {selected.reply}</p>
              )}

              {selected.status === 'completed' && (
                <>
                  <div>
                    <h3 className="text-sm font-bold text-white mb-2">결과</h3>
                    <p className="text-sm text-[#E6EDF7] whitespace-pre-wrap leading-relaxed">{selected.result_summary}</p>
                  </div>
                  {selected.files.length > 0 && (
                    <div>
                      <h3 className="text-sm font-bold text-white mb-2">첨부 파일</h3>
                      <ul className="space-y-1">
                        {selected.files.map(f => (
                          <li key={f.id}>
                            <button type="button" onClick={() => downloadAnalysisFile(selected.id, f).catch(e => setError(e.message))}
                              className="text-sm text-[#00E5CC] hover:underline"><i className="ri-download-2-line mr-1"></i>{f.filename} <span className="text-[#4A5568]">({kb(f.size)})</span></button>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                      <h4 className="text-xs font-semibold text-[#8B9BB4] mb-1">방법</h4>
                      <p className="text-xs text-[#C9D3E3] whitespace-pre-wrap leading-relaxed">{selected.result_method}</p>
                    </div>
                    <div>
                      <h4 className="text-xs font-semibold text-[#8B9BB4] mb-1">데이터 기준일</h4>
                      <p className="text-xs text-[#C9D3E3]">{selected.result_data_as_of}</p>
                      <h4 className="text-xs font-semibold text-[#8B9BB4] mt-3 mb-1">사용한 데이터·출처</h4>
                      <ul className="text-xs text-[#C9D3E3] list-disc pl-4 space-y-0.5">{selected.result_sources.map((s, i) => <li key={i}>{sourceText(s)}</li>)}</ul>
                    </div>
                  </div>
                  {selected.result_assumptions.length > 0 && (
                    <div>
                      <h4 className="text-xs font-semibold text-[#8B9BB4] mb-1">적용한 가정</h4>
                      <ul className="text-xs text-[#C9D3E3] list-disc pl-4 space-y-0.5">{selected.result_assumptions.map((s, i) => <li key={i}>{sourceText(s)}</li>)}</ul>
                    </div>
                  )}
                  {selected.result_limitations && (
                    <div className="rounded-xl border border-[#1E2530] bg-[#0D1117] p-3">
                      <h4 className="text-xs font-semibold text-[#F59E0B] mb-1">한계·주의</h4>
                      <p className="text-xs text-[#C9D3E3] whitespace-pre-wrap leading-relaxed">{selected.result_limitations}</p>
                    </div>
                  )}
                  <p className="text-[11px] text-[#4A5568]">분석 담당(Aside)이 작성한 결과입니다. 의사결정에 쓰기 전 가정과 출처를 확인하세요.</p>
                </>
              )}

              {selected.status === 'failed' && (
                <div className="rounded-xl border border-red-500/30 bg-red-500/5 p-4">
                  <p className="text-xs font-semibold text-red-400 mb-1">처리하지 못한 이유</p>
                  <p className="text-sm text-white whitespace-pre-wrap">{selected.failure_reason}</p>
                </div>
              )}
              {(selected.status === 'queued' || selected.status === 'processing') && (
                <p className="text-sm text-[#8B9BB4]">{selected.status === 'queued' ? '접수됨 — 분석 담당이 곧 가져갑니다.' : '분석 중입니다.'}</p>
              )}
              {isMine && (selected.status === 'queued' || selected.status === 'needs_info') && (
                <button type="button" disabled={busy} onClick={() => act(() => cancelAnalysisRequest(selected.id))}
                  className="text-xs text-[#8B9BB4] hover:text-red-400">요청 취소</button>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
