import { useState } from 'react';
import { fetchRun, type SVRun, type SVRunDetail, type SVRunKind } from '@/api/stakeholder';
import { Modal } from './ui';
import { fmtDateTime } from './uiHelpers';

// 런 이력 — 모니터링(일일)·감사(커버리지) 최근 10건씩. 행 클릭 → 런 상세 모달(MD 본문 <pre> + payload JSON 접기).
// react-markdown 은 프로젝트 의존성에 없어 MD 는 원문 그대로 <pre> 로 보여준다(HTML 해석 없음).

function runStatusTone(s?: string | null): string {
  const v = (s || '').toLowerCase();
  if (v === 'completed' || v === 'ok' || v === 'success' || v === 'complete') return 'bg-[#00E5CC]/10 text-[#00E5CC]';
  if (v === 'degraded' || v === 'partial') return 'bg-[#F59E0B]/10 text-[#F59E0B]';
  if (v === 'failed' || v === 'error') return 'bg-red-500/10 text-red-400';
  return 'bg-[#1E2530] text-[#8B9BB4]';
}

export function RunStatus({ status }: { status?: string | null }) {
  return (
    <span className={`inline-block rounded px-1.5 py-0.5 text-[10px] font-semibold whitespace-nowrap ${runStatusTone(status)}`}>
      {status || '—'}
    </span>
  );
}

function runIdOf(r: SVRun): string {
  return r.run_id || r.audit_run_id || '';
}

function RunTable({
  kind,
  runs,
  loading,
  error,
  onOpen,
}: {
  kind: SVRunKind;
  runs: SVRun[];
  loading: boolean;
  error: string | null;
  onOpen: (r: SVRun) => void;
}) {
  const title = kind === 'monitoring' ? '일일 모니터링 런' : '커버리지 감사 런';
  return (
    <section className="bg-[#161B27] rounded-2xl border border-[#1E2530] overflow-hidden">
      <h3 className="px-4 py-3 border-b border-[#1E2530] text-xs font-bold text-white">
        {title} <span className="text-[#4A5568] font-normal">최근 {runs.length}건</span>
      </h3>
      {loading ? (
        <div className="p-4 space-y-2 animate-pulse">
          {[0, 1, 2].map(i => (
            <div key={i} className="h-8 rounded bg-[#1E2530]" />
          ))}
        </div>
      ) : error ? (
        <p className="p-4 text-xs text-red-400">런 이력을 불러오지 못했습니다 — {error}</p>
      ) : runs.length === 0 ? (
        <p className="p-4 text-xs text-[#4A5568]">아직 적재된 런이 없습니다.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-[10px] text-[#4A5568] text-left">
              <tr>
                <th className="px-4 py-2 font-semibold">실행일</th>
                <th className="px-2 py-2 font-semibold">상태</th>
                {kind === 'monitoring' ? (
                  <>
                    <th className="px-2 py-2 font-semibold">saturation</th>
                    <th className="px-2 py-2 font-semibold text-right">활성 인물</th>
                    <th className="px-2 py-2 font-semibold text-right">승인 출처</th>
                    <th className="px-4 py-2 font-semibold text-right">신규 인용</th>
                  </>
                ) : (
                  <>
                    <th className="px-2 py-2 font-semibold text-right">생성 제안</th>
                    <th className="px-4 py-2 font-semibold">주장(claim)</th>
                  </>
                )}
              </tr>
            </thead>
            <tbody>
              {runs.map(r => (
                <tr
                  key={runIdOf(r)}
                  tabIndex={0}
                  role="button"
                  aria-label={`${title} ${runIdOf(r)} 상세`}
                  onClick={() => onOpen(r)}
                  onKeyDown={e => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      onOpen(r);
                    }
                  }}
                  className="border-t border-[#1E2530] hover:bg-white/5 cursor-pointer"
                >
                  <td className="px-4 py-2 text-white whitespace-nowrap">
                    {r.run_date || '—'}
                    <span className="block font-mono text-[10px] text-[#4A5568]">{runIdOf(r)}</span>
                  </td>
                  <td className="px-2 py-2">
                    <RunStatus status={r.run_status} />
                  </td>
                  {kind === 'monitoring' ? (
                    <>
                      <td className="px-2 py-2">
                        <RunStatus status={r.saturation_status} />
                      </td>
                      <td className="px-2 py-2 text-right text-[#C9D1DD]">{r.active_people ?? '—'}</td>
                      <td className="px-2 py-2 text-right text-[#C9D1DD]">{r.approved_domains ?? '—'}</td>
                      <td className="px-4 py-2 text-right text-[#C9D1DD]">{r.new_quotes ?? '—'}</td>
                    </>
                  ) : (
                    <>
                      <td className="px-2 py-2 text-right text-[#C9D1DD]">{r.proposals_created ?? '—'}</td>
                      <td className="px-4 py-2 text-[#8B9BB4] max-w-[28rem]">
                        <span className="line-clamp-2">{r.claim || '—'}</span>
                      </td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export default function RunsPanel({
  monitoring,
  audit,
}: {
  monitoring: { data: SVRun[]; loading: boolean; error: string | null };
  audit: { data: SVRun[]; loading: boolean; error: string | null };
}) {
  const [open, setOpen] = useState<{ kind: SVRunKind; run: SVRun } | null>(null);
  const [detail, setDetail] = useState<SVRunDetail | null>(null);
  const [detailErr, setDetailErr] = useState<string | null>(null);
  const [showPayload, setShowPayload] = useState(false);

  const openRun = (kind: SVRunKind, run: SVRun) => {
    setOpen({ kind, run });
    setDetail(null);
    setDetailErr(null);
    setShowPayload(false);
    fetchRun(kind, runIdOf(run))
      .then(setDetail)
      .catch(e => setDetailErr(e instanceof Error ? e.message : '런 상세를 불러오지 못했습니다.'));
  };

  return (
    <div className="space-y-4">
      <RunTable kind="monitoring" {...monitoring} runs={monitoring.data} onOpen={r => openRun('monitoring', r)} />
      <RunTable kind="audit" {...audit} runs={audit.data} onOpen={r => openRun('audit', r)} />
      {open && (
        <Modal
          wide
          title={`${open.kind === 'monitoring' ? '모니터링' : '감사'} 런 · ${open.run.run_date ?? ''} · ${runIdOf(open.run)}`}
          onClose={() => setOpen(null)}
        >
          {detailErr ? (
            <p className="text-xs text-red-400">런 상세를 불러오지 못했습니다 — {detailErr}</p>
          ) : !detail ? (
            <p className="text-xs text-[#8B9BB4]">불러오는 중…</p>
          ) : (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2 text-[11px] text-[#8B9BB4]">
                <RunStatus status={detail.run_status ?? open.run.run_status} />
                {detail.saturation_status && <RunStatus status={detail.saturation_status} />}
                <span>실행 {fmtDateTime(detail.executed_at ?? open.run.executed_at)}</span>
                {detail.imported_at && <span>· 적재 {fmtDateTime(detail.imported_at)}</span>}
              </div>
              {(detail.claim || detail.why_this_matters) && (
                <div className="rounded-xl bg-[#0D1117] border border-[#1E2530] p-3 text-xs space-y-1">
                  {detail.claim && <p className="text-white font-semibold">{detail.claim}</p>}
                  {detail.why_this_matters && <p className="text-[#8B9BB4]">{detail.why_this_matters}</p>}
                </div>
              )}
              {detail.md ? (
                <pre className="max-h-[55vh] overflow-auto rounded-xl bg-[#0D1117] border border-[#1E2530] p-3 text-[11px] leading-relaxed text-[#C9D1DD] whitespace-pre-wrap break-words">
                  {detail.md}
                </pre>
              ) : (
                <p className="text-xs text-[#4A5568]">MD 본문이 없습니다.</p>
              )}
              {detail.payload && (
                <div>
                  <button
                    className="text-[11px] text-[#00E5CC] hover:underline cursor-pointer"
                    onClick={() => setShowPayload(v => !v)}
                  >
                    {showPayload ? '원본 JSON 접기' : '원본 JSON 보기'}
                  </button>
                  {showPayload && (
                    <pre className="mt-2 max-h-[45vh] overflow-auto rounded-xl bg-[#0D1117] border border-[#1E2530] p-3 text-[10px] text-[#8B9BB4]">
                      {JSON.stringify(detail.payload, null, 2)}
                    </pre>
                  )}
                </div>
              )}
            </div>
          )}
        </Modal>
      )}
    </div>
  );
}
