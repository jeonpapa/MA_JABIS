import type { SVIntegrityExcluded, SVIntegrityProblem, SVIntegrityReport, SVSource } from '@/api/stakeholder';
import { IntegrityBadge } from '@/pages/stakeholder-signals/components/GradeBadge';
import { fmtKoDateTime } from '@/pages/stakeholder-signals/sketch/helpers';
import { BAD_STATUSES, CHECKED_STATUSES, INTEGRITY_TILE_LABELS, NEUTRAL_STATUSES } from '../labels';

// 출처 무결성 — GET /api/admin/stakeholder/integrity
//   {summary, last_sync_at, counts, artifact_counts, problems[], artifact_problems[], excluded[]}
// 분모(검증 대상) = 전체 − no_local − excluded (서버 overview.sources_total 정의와 같음).
// no_local(로컬 사본 없는 영상 등)·excluded(sync 허용목록 밖 — 설계상 미러 안 함)는 실패가 아니라 중립 회색.
// 구 모니터링 감사 탭(commit c51a57d2 IntegrityPanel)을 스케치 004 시각 언어로 옮기고 excluded 를 더했다.

function shortSha(s?: string | null): string {
  if (!s) return '—';
  return s.length > 16 ? `${s.slice(0, 12)}…` : s;
}

function CountsCard({ title, table, counts }: { title: string; table: string; counts?: Record<string, number> | null }) {
  const c = counts ?? {};
  const known = new Set<string>([...CHECKED_STATUSES, ...NEUTRAL_STATUSES]);
  const others = Object.keys(c).filter(k => !known.has(k));
  const total = Object.entries(c)
    .filter(([k]) => !(NEUTRAL_STATUSES as readonly string[]).includes(k))
    .reduce((n, [, v]) => n + v, 0);
  const verified = c.verified ?? 0;
  const empty = Object.keys(c).length === 0;
  return (
    <section className="panel">
      <div className="integ-head">
        <b>
          {title}
          <code>{table}</code>
        </b>
        {!empty && (
          <span className="ratio">
            일치
            <strong className={verified === total ? 'ok' : 'bad'}>
              {verified}/{total}
            </strong>
          </span>
        )}
      </div>
      {empty ? (
        <p className="meta" style={{ margin: 0 }}>
          집계 없음 — 아직 sync 전일 수 있습니다.
        </p>
      ) : (
        <>
          <div className="tiles">
            {[...CHECKED_STATUSES, ...others].map(k => {
              const v = c[k] ?? 0;
              return (
                <div key={k} className={`tile${BAD_STATUSES.has(k) && v > 0 ? ' bad' : ''}`}>
                  {INTEGRITY_TILE_LABELS[k] ?? k}
                  <b>{v}</b>
                </div>
              );
            })}
            {NEUTRAL_STATUSES.map(k => (
              <div key={k} className="tile neutral" title="무결성 분모에서 제외 — 실패 아님">
                {INTEGRITY_TILE_LABELS[k]}
                <b>{c[k] ?? 0}</b>
              </div>
            ))}
          </div>
          <p className="meta" style={{ marginBottom: 0 }}>
            분모 {total}건 = 전체 − 로컬 원문 없음 − 의도적 제외
          </p>
        </>
      )}
    </section>
  );
}

function ProblemTable({ title, rows, artifact }: { title: string; rows: SVIntegrityProblem[]; artifact?: boolean }) {
  if (!rows.length) return null;
  return (
    <section className="panel bad-panel">
      <b>
        {title} {rows.length}건
      </b>
      <div className="scroll">
        <table className="tbl">
          <thead>
            <tr>
              <th>{artifact ? '경로' : '출처'}</th>
              <th>상태</th>
              <th>{artifact ? '이름' : '파일'}</th>
              <th>기대 SHA-256</th>
              <th>검사 시각</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={r.source_id ?? r.path ?? i}>
                <td style={{ fontWeight: 700, wordBreak: 'break-all' }}>
                  {artifact ? r.path : r.source_id}
                  {!artifact && r.title && <span style={{ display: 'block', fontWeight: 400, color: '#666' }}>{r.title}</span>}
                </td>
                <td>
                  <IntegrityBadge status={r.integrity_status} />
                </td>
                <td className="mono">{artifact ? r.name ?? '—' : [r.local_path, r.text_path].filter(Boolean).join(' · ') || '—'}</td>
                <td className="mono" title={r.sha256 ?? undefined}>
                  {shortSha(r.sha256)}
                  {!artifact && r.text_sha256 && (
                    <span style={{ display: 'block' }} title={r.text_sha256}>
                      txt {shortSha(r.text_sha256)}
                    </span>
                  )}
                </td>
                <td style={{ whiteSpace: 'nowrap' }}>{fmtKoDateTime(r.integrity_checked_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/** summary(sv_meta integrity_summary_json).excluded 에만 사유가 있다 — kind+id 로 붙인다. */
function reasonsFrom(summary: SVIntegrityReport['summary']): Map<string, string> {
  const out = new Map<string, string>();
  const raw = summary && Array.isArray((summary as { excluded?: unknown }).excluded) ? (summary as { excluded: unknown[] }).excluded : [];
  raw.forEach(x => {
    if (!x || typeof x !== 'object') return;
    const e = x as SVIntegrityExcluded;
    const r = Array.isArray(e.reason) ? e.reason.join(' · ') : typeof e.reason === 'string' ? e.reason : '';
    if (e.id && r) out.set(`${e.kind}:${e.id}`, r);
  });
  return out;
}

const KIND_LABEL: Record<string, string> = { source: '원문 출처', artifact: '산출물' };

export default function IntegritySection({
  report,
  loading,
  error,
  noLocal,
}: {
  report: SVIntegrityReport | null;
  loading: boolean;
  error: string | null;
  /** 로컬 사본이 없는 출처(/api/stakeholder/sources 의 integrity_status=no_local) */
  noLocal: SVSource[];
}) {
  const head = (
    <div className="sec">
      <div>
        <h2>출처 무결성</h2>
        <p>sync 가 원문·산출물 파일의 SHA-256 을 manifest 와 다시 대조한 결과입니다.</p>
      </div>
      {report && <span className="meta side">무결성 검사 {fmtKoDateTime(checkedAt(report) ?? report.last_sync_at)}</span>}
    </div>
  );
  if (loading)
    return (
      <>
        {head}
        <div className="skel" aria-label="무결성 요약 불러오는 중" />
      </>
    );
  if (error)
    return (
      <>
        {head}
        <section className="panel">
          <div className="empty">
            <b>무결성 요약을 불러오지 못했습니다.</b>
            <p>{error}</p>
          </div>
        </section>
      </>
    );
  if (!report) return null;

  const problems = report.problems ?? [];
  const artProblems = report.artifact_problems ?? [];
  const excluded = report.excluded ?? [];
  const reasons = reasonsFrom(report.summary);

  return (
    <>
      {head}
      <div className="integ">
        <CountsCard title="원문 출처" table="sv_source" counts={report.counts} />
        <CountsCard title="정본·산출물" table="sv_artifact" counts={report.artifact_counts} />
      </div>
      {problems.length === 0 && artProblems.length === 0 && <p className="clean">불일치·누락 파일 없음</p>}
      <ProblemTable title="원문 출처 문제" rows={problems} />
      <ProblemTable title="산출물 문제" rows={artProblems} artifact />
      {excluded.length > 0 && (
        <section className="panel neutral-list" aria-label="의도적 제외 목록">
          <b>의도적 제외 — 동기화 대상 아님 (실패 아님) · {excluded.length}건</b>
          <p>manifest 에는 있지만 sync 허용목록 밖(서비스 코드·대시보드 스케치 등)이라 설계상 받지 않습니다. 무결성 분모에서 뺍니다.</p>
          <ul>
            {excluded.map(e => (
              <li key={`${e.kind}:${e.id}`}>
                <span className="svb neutral">{KIND_LABEL[e.kind] ?? e.kind}</span>
                <span>
                  <code>{e.id}</code>
                  {reasons.get(`${e.kind}:${e.id}`) && <small>{reasons.get(`${e.kind}:${e.id}`)}</small>}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
      {noLocal.length > 0 && (
        <section className="panel neutral-list" aria-label="로컬 원문 없음 목록">
          <b>로컬 원문 없음 — 영상 등 · {noLocal.length}건</b>
          <p>원문 사본 파일이 없는 출처라 SHA-256 대조 대상이 아닙니다. URL 로만 확인합니다. 무결성 분모에서 뺍니다.</p>
          <ul>
            {noLocal.map(s => (
              <li key={s.source_id}>
                <span className="svb neutral">원문 출처</span>
                <span>
                  <code>{s.source_id}</code>
                  {s.title && <small>{s.title}</small>}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

function checkedAt(r: SVIntegrityReport): string | null {
  const v = r.summary && (r.summary as { checked_at?: unknown }).checked_at;
  return typeof v === 'string' ? v : null;
}
