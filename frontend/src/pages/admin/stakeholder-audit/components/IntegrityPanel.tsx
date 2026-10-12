import type { SVIntegrityProblem, SVIntegrityReport } from '@/api/stakeholder';
import { IntegrityBadge } from '@/pages/stakeholder-signals/components/GradeBadge';
import { fmtDateTime } from './uiHelpers';
import { boxCard, card, errorBox, thead } from '@/pages/stakeholder-signals/theme/sv';

// 출처 무결성 패널 — GET /api/admin/stakeholder/integrity
//   {summary, last_sync_at, counts, artifact_counts, problems[], artifact_problems[]}
// counts/artifact_counts 는 integrity_status → 개수. problems 는 mismatch/missing 행(admin 전용이라 경로·SHA 포함).
// summary(sv_meta, sync 소유)는 모양이 바뀔 수 있어 스칼라 값만 키-값으로 보여준다.

const STATUS_ORDER = ['verified', 'mismatch', 'missing', 'no_local', 'unchecked'];
const STATUS_LABELS: Record<string, string> = {
  verified: 'SHA-256 일치',
  mismatch: '불일치',
  missing: '파일 없음',
  no_local: '로컬 원문 없음',
  unchecked: '미검증',
};
const BAD = new Set(['mismatch', 'missing']);

function shortSha(s?: string | null): string {
  if (!s) return '—';
  return s.length > 16 ? `${s.slice(0, 12)}…` : s;
}

function CountsRow({ title, counts }: { title: string; counts?: Record<string, number> | null }) {
  const rank = (k: string) => (STATUS_ORDER.includes(k) ? STATUS_ORDER.indexOf(k) : 99);
  const entries = Object.entries(counts ?? {}).sort(([a], [b]) => rank(a) - rank(b));
  // no_local 은 원문 사본이 없는 출처(영상 등) — 무결성 분모에서 뺀다(서버 sources_total 정의와 같음)
  const checkable = entries.filter(([k]) => k !== 'no_local').reduce((n, [, v]) => n + v, 0);
  const verified = counts?.verified ?? 0;
  return (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-2 mb-2">
        <p className="text-[12px] font-bold text-sv-text">{title}</p>
        {entries.length > 0 && (
          <p className="text-[12px] text-sv-text">
            일치 <span className={verified === checkable ? 'text-sv-ok-fg font-extrabold' : 'text-sv-bad-fg font-extrabold'}>{verified}/{checkable}</span>
            <span className="text-sv-muted"> (로컬 원문 없음 제외)</span>
          </p>
        )}
      </div>
      {entries.length === 0 ? (
        <p className="text-[13px] text-sv-muted">집계 없음(아직 sync 전일 수 있습니다).</p>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2">
          {entries.map(([k, v]) => (
            <div
              key={k}
              className={`rounded-lg border px-3 py-2 ${BAD.has(k) && v > 0 ? 'border-sv-bad-line bg-sv-bad-bg' : 'border-sv-line bg-sv-surface'}`}
            >
              <p className="text-[11px] text-sv-muted">{STATUS_LABELS[k] ?? k}</p>
              <p className={`text-[19px] font-extrabold ${BAD.has(k) && v > 0 ? 'text-sv-bad-fg' : 'text-sv-ink'}`}>{v}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ProblemTable({ title, rows, artifact }: { title: string; rows: SVIntegrityProblem[]; artifact?: boolean }) {
  if (!rows.length) return null;
  return (
    <section className="bg-sv-surface rounded-[10px] border-2 border-sv-bad-strong overflow-hidden">
      <h3 className="px-4 py-3 border-b border-sv-bad-line bg-sv-bad-bg text-[13px] font-extrabold text-sv-bad-fg">
        {title} {rows.length}건
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full text-[12px]">
          <thead className={`${thead} text-left`}>
            <tr>
              <th className="px-3 py-2 font-semibold">{artifact ? '경로' : '출처'}</th>
              <th className="px-3 py-2 font-semibold">상태</th>
              <th className="px-3 py-2 font-semibold">{artifact ? '이름' : '파일'}</th>
              <th className="px-3 py-2 font-semibold">기대 SHA-256</th>
              <th className="px-3 py-2 font-semibold">검사 시각</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={r.source_id ?? r.path ?? i} className="border-t border-sv-line-soft align-top">
                <td className="px-3 py-2 text-sv-ink font-semibold break-all">
                  {artifact ? r.path : r.source_id}
                  {!artifact && r.title && <span className="block font-normal text-sv-text">{r.title}</span>}
                </td>
                <td className="px-3 py-2">
                  <IntegrityBadge status={r.integrity_status} />
                </td>
                <td className="px-3 py-2 font-mono text-[11px] text-sv-text break-all">
                  {artifact ? r.name ?? '—' : [r.local_path, r.text_path].filter(Boolean).join(' · ') || '—'}
                </td>
                <td className="px-3 py-2 font-mono text-[11px] text-sv-text" title={r.sha256 ?? undefined}>
                  {shortSha(r.sha256)}
                  {!artifact && r.text_sha256 && <span className="block" title={r.text_sha256}>txt {shortSha(r.text_sha256)}</span>}
                </td>
                <td className="px-3 py-2 text-sv-text whitespace-nowrap">{fmtDateTime(r.integrity_checked_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function IntegrityPanel({
  report,
  loading,
  error,
}: {
  report: SVIntegrityReport | null;
  loading: boolean;
  error: string | null;
}) {
  if (loading) {
    return <div className={`h-32 ${boxCard} animate-pulse`} />;
  }
  if (error) {
    return (
      <div className={errorBox}>
        무결성 요약을 불러오지 못했습니다 — {error}
      </div>
    );
  }
  if (!report) return null;

  const problems = report.problems ?? [];
  const artProblems = report.artifact_problems ?? [];
  const summaryScalars = Object.entries(report.summary ?? {}).filter(
    ([, v]) => v === null || ['string', 'number', 'boolean'].includes(typeof v),
  );

  return (
    <div className="space-y-4">
      <div className={`${card} p-4 sm:p-5 space-y-5`}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-[14px] font-extrabold text-sv-ink">출처 무결성</h3>
          <span className="text-[12px] text-sv-muted">마지막 sync {fmtDateTime(report.last_sync_at)}</span>
        </div>
        <CountsRow title="원문 출처(sv_source)" counts={report.counts} />
        <CountsRow title="정본·산출물(sv_artifact)" counts={report.artifact_counts} />
        {problems.length === 0 && artProblems.length === 0 && (
          <p className="text-[12px] font-bold text-sv-ok-fg">
            <i className="ri-shield-check-line mr-1"></i>불일치·누락 파일 없음
          </p>
        )}
        {summaryScalars.length > 0 && (
          <details>
            <summary className="cursor-pointer text-[12px] font-semibold text-sv-text hover:text-sv-ink">sync 무결성 요약(원본)</summary>
            <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[12px]">
              {summaryScalars.map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="text-sv-muted font-mono">{k}</dt>
                  <dd className="text-sv-ink break-all">{v === null ? '—' : String(v)}</dd>
                </div>
              ))}
            </dl>
          </details>
        )}
      </div>
      <ProblemTable title="원문 출처 문제" rows={problems} />
      <ProblemTable title="산출물 문제" rows={artProblems} artifact />
    </div>
  );
}
