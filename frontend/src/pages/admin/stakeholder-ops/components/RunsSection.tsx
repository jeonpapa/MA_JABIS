import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { fetchRun, svErrorMessage, type SVRun, type SVRunDetail, type SVRunKind } from '@/api/stakeholder';
import { fmtKoDateTime } from '@/pages/stakeholder-signals/sketch/helpers';
import { runStatusMeta } from '../labels';

// 런 이력 — 일일 모니터링 / 커버리지 감사 (GET /api/stakeholder/runs?kind=…).
// 행 → 런 상세 모달(GET /api/stakeholder/runs/<kind>/<id>): 요약 + MD 원문 <pre>(HTML 해석 없음) + payload JSON 접기.
// 구 모니터링 감사 탭(commit c51a57d2 RunsPanel)을 스케치 004 시각 언어로 옮긴 것.

export interface RunsState {
  data: SVRun[];
  loading: boolean;
  error: string | null;
}

export function RunBadge({ status }: { status?: string | null }) {
  const m = runStatusMeta(status);
  return (
    <span className={`svb ${m.tone}`} title={m.raw || undefined}>
      {m.label}
    </span>
  );
}

const runIdOf = (r: SVRun): string => r.run_id || r.audit_run_id || '';
const KIND_LABEL: Record<SVRunKind, string> = { monitoring: '일일 모니터링', audit: '커버리지 감사' };

function RunModal({ kind, run, onClose }: { kind: SVRunKind; run: SVRun; onClose: () => void }) {
  const [detail, setDetail] = useState<SVRunDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [showPayload, setShowPayload] = useState(false);
  const titleId = useId();
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    let alive = true;
    fetchRun(kind, runIdOf(run))
      .then(d => alive && setDetail(d))
      .catch(e => alive && setErr(svErrorMessage(e, '런 상세를 불러오지 못했습니다.')));
    return () => {
      alive = false;
    };
  }, [kind, run]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    closeRef.current?.focus();
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const d = detail;
  return (
    <div className="sxm-wrap" onClick={onClose}>
      <div className="sxm wide" role="dialog" aria-modal="true" aria-labelledby={titleId} onClick={e => e.stopPropagation()}>
        <div className="sxm-head">
          <h2 id={titleId}>
            {KIND_LABEL[kind]} 런 · {run.run_date ?? '—'}
          </h2>
          <button ref={closeRef} onClick={onClose} aria-label="닫기">
            ×
          </button>
        </div>
        <div className="sxm-body">
          {err ? (
            <div role="alert" className="svx-err" style={{ marginTop: 0 }}>
              {err}
            </div>
          ) : !d ? (
            <p>불러오는 중…</p>
          ) : (
            <>
              <div className="svb-row">
                <RunBadge status={d.run_status ?? run.run_status} />
                {kind === 'monitoring' && <RunBadge status={d.saturation_status ?? run.saturation_status} />}
                <span className="meta" style={{ margin: 0 }}>
                  <code>{runIdOf(run)}</code> · 실행 {fmtKoDateTime(d.executed_at ?? run.executed_at)}
                  {d.imported_at ? ` · 적재 ${fmtKoDateTime(d.imported_at)}` : ''}
                </span>
              </div>
              {kind === 'monitoring' ? (
                <div className="change">
                  활성 인물 {d.active_people ?? '—'}명 · 승인 출처 {d.approved_domains ?? '—'}개 · 신규 인용 {d.new_quotes ?? '—'}건
                </div>
              ) : (
                (d.claim || d.why_this_matters || d.proposals_created != null) && (
                  <div className="change">
                    {d.claim && <b style={{ display: 'block' }}>{d.claim}</b>}
                    {d.why_this_matters && <span style={{ display: 'block', color: '#555' }}>{d.why_this_matters}</span>}
                    <span style={{ display: 'block', color: '#777' }}>생성 제안 {d.proposals_created ?? '—'}건</span>
                  </div>
                )
              )}
              {d.md ? <pre className="md">{d.md}</pre> : <p style={{ marginTop: 12 }}>MD 본문이 없습니다.</p>}
              {d.payload && (
                <>
                  <button className="svx-linkbtn" style={{ marginTop: 12 }} onClick={() => setShowPayload(v => !v)} aria-expanded={showPayload}>
                    {showPayload ? '원본 JSON 접기' : '원본 JSON 보기'}
                  </button>
                  {showPayload && <pre className="raw">{JSON.stringify(d.payload, null, 2)}</pre>}
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default function RunsSection({ monitoring, audit }: { monitoring: RunsState; audit: RunsState }) {
  const [kind, setKind] = useState<SVRunKind>('monitoring');
  const [open, setOpen] = useState<SVRun | null>(null);
  const cur = kind === 'monitoring' ? monitoring : audit;
  const close = useCallback(() => setOpen(null), []);

  return (
    <>
      <div className="sec">
        <div>
          <h2>런 이력</h2>
          <p>모니터링 에이전트 매일 07:00 · 감사 에이전트 매일 07:40 KST. 행을 누르면 런 보고서(MD)를 봅니다.</p>
        </div>
        <div className="side chips" role="group" aria-label="런 종류">
          {(['monitoring', 'audit'] as SVRunKind[]).map(k => {
            const s = k === 'monitoring' ? monitoring : audit;
            return (
              <button key={k} className={`chip${kind === k ? ' active' : ''}`} onClick={() => setKind(k)} aria-pressed={kind === k}>
                {KIND_LABEL[k]} {s.loading ? '' : s.data.length}
              </button>
            );
          })}
        </div>
      </div>
      <section className="panel" aria-live="polite">
        {cur.loading ? (
          <div className="empty">런 이력을 불러오는 중입니다.</div>
        ) : cur.error ? (
          <div className="empty">
            <b>런 이력을 불러오지 못했습니다.</b>
            <p>{cur.error}</p>
          </div>
        ) : cur.data.length === 0 ? (
          <div className="empty">아직 적재된 {KIND_LABEL[kind]} 런이 없습니다.</div>
        ) : (
          <div className="scroll">
            <table className="tbl">
              <thead>
                <tr>
                  <th>실행일</th>
                  <th>상태</th>
                  {kind === 'monitoring' ? (
                    <>
                      <th>saturation</th>
                      <th className="num">활성 인물</th>
                      <th className="num">승인 출처</th>
                      <th className="num">신규 인용</th>
                    </>
                  ) : (
                    <>
                      <th className="num">생성 제안</th>
                      <th>주장(claim)</th>
                    </>
                  )}
                </tr>
              </thead>
              <tbody>
                {cur.data.map(r => (
                  <tr
                    key={runIdOf(r)}
                    className="click"
                    tabIndex={0}
                    aria-label={`${KIND_LABEL[kind]} 런 ${runIdOf(r)} 상세`}
                    onClick={() => setOpen(r)}
                    onKeyDown={e => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        setOpen(r);
                      }
                    }}
                  >
                    <td>
                      <span className="date">{r.run_date || '—'}</span>
                      <span className="id">{runIdOf(r)}</span>
                    </td>
                    <td>
                      <RunBadge status={r.run_status} />
                    </td>
                    {kind === 'monitoring' ? (
                      <>
                        <td>
                          <RunBadge status={r.saturation_status} />
                        </td>
                        <td className="num">{r.active_people ?? '—'}</td>
                        <td className="num">{r.approved_domains ?? '—'}</td>
                        <td className="num">{r.new_quotes ?? '—'}</td>
                      </>
                    ) : (
                      <>
                        <td className="num">{r.proposals_created ?? '—'}</td>
                        <td style={{ minWidth: 260 }}>
                          <span className="clamp">{r.claim || '—'}</span>
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
      {open && <RunModal kind={kind} run={open} onClose={close} />}
    </>
  );
}
