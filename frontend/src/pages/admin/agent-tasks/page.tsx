import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { fetchMe } from '@/utils/authUsers';
import {
  fetchAgentTasks, fetchAgentTaskDetail, updateAgentPolicy,
  type AgentTask, type AgentPolicy, type AgentTaskEvent, type AgentTasksResponse, type AgentTaskDetailResponse,
} from '@/api/agentTasks';

// 운영 원장 (admin) — 설계 §7: 오늘 처리한 작업·상태·근거·배포 버전·비용, Daily Mailing 칸반과 같은 구조.
// 정지 스위치(§8): agent_policy.enabled=false 한 줄로 팀 전체 정지. 기존 스케줄러(수집·발송)는 계속 돈다.
// 모든 필드 접근은 부재/null 을 허용한다 (오래된 행·빈 목록·백엔드 부분 구현).

const Dash = () => <span className="text-[#4A5568]">—</span>;

const KIND_LABELS: Record<string, string> = {
  ops_incident: '운영 장애',
  user_request: '사용자 요청',
  improvement: '개선',
  release: '릴리스',
};

// P1 에서 실제로 쓰이는 값은 signals.py 의 두 가지(scheduler_job·daily_mailing_health). 나머지는 설계 §5 의 예정 값.
const SOURCE_LABELS: Record<string, string> = {
  scheduler_job: '스케줄러 잡 실패',
  scheduler_missed: '스케줄러 놓친 실행',
  daily_mailing_health: '메일링 헬스체크',
  domain_freshness: '데이터 신선도',
  foreign_search: '해외약가 수집',
  known_debt: '구현 대기 부채',
  health_check: '헬스 체크',
  quality_guard: 'Quality Guard',
  rule_compliance: 'Rule Compliance',
  service_request: '서비스 요청',
  mail_stats: '메일 통계',
  manual: '수동',
};

// 담당(owner) — agents/agent_tasks/owner.py · docs/ops/OWNERSHIP.md. 서버가 source·source_ref·런북 힌트로 결정적으로 계산한다.
//   server = 프로덕션 런북으로 풀림 · aside = 해외약가(노트북 Aside 브라우저 에이전트) · hermes = 판단·큐레이션(외부 VPS Hermes 팀)
const OWNER_LABELS: Record<string, string> = {
  server: '서버 크론',
  aside: 'Aside',
  hermes: 'Hermes',
};
const OWNER_HINTS: Record<string, string> = {
  server: '프로덕션 런북으로 복구 — 디스패처 --owner server 가 트리거',
  aside: '해외약가(A8/OECD·증빙·로그인 소스) — 노트북 Aside 가 /api/agent/tasks?owner=aside 로 처리',
  hermes: '판단·큐레이션·에스컬레이션 — 외부 VPS Hermes 팀 (디스패처 기본 --owner hermes)',
};
const OWNER_FILTERS = ['all', 'server', 'aside', 'hermes', 'unknown'] as const;
type OwnerFilter = typeof OWNER_FILTERS[number];

function ownerOf(task: AgentTask): string {
  const v = (task.owner || '').toLowerCase();
  return v && OWNER_LABELS[v] ? v : 'unknown';
}

function ownerTone(owner: string): string {
  if (owner === 'server') return 'bg-[#3B82F6]/10 text-[#60A5FA]';
  if (owner === 'aside') return 'bg-[#00E5CC]/10 text-[#00E5CC]';
  if (owner === 'hermes') return 'bg-[#7C3AED]/10 text-[#A78BFA]';
  return 'bg-[#1E2530] text-[#8B9BB4]';
}

function OwnerBadge({ task }: { task: AgentTask }) {
  const owner = ownerOf(task);
  return (
    <span className={`text-[10px] px-2 py-0.5 rounded whitespace-nowrap ${ownerTone(owner)}`} title={OWNER_HINTS[owner] ?? '담당 미분류(구버전 서버)'}>
      {OWNER_LABELS[owner] ?? '미분류'}
    </span>
  );
}

const STATUS_LABELS: Record<string, string> = {
  new: '신규',
  triaged: '분류됨',
  in_progress: '진행 중',
  verified: '검증 완료',
  awaiting_approval: '승인 대기',
  deploying: '배포 중',
  deployed: '배포됨',
  closed: '종료',
  blocked: '막힘',
  rejected: '기각',
};

// 상태 색 — 계획 Task 8: closed=teal, blocked/rejected=red, in_progress=amber, 그 외 blue.
// 같은 계열로 deployed/verified 는 teal, awaiting_approval/deploying 은 amber(사람·배포 대기).
function statusTone(status: string | null | undefined): string {
  const v = (status || '').toLowerCase();
  if (!v) return 'bg-[#1E2530] text-[#8B9BB4]';
  if (['closed', 'deployed', 'verified'].includes(v)) return 'bg-[#00E5CC]/10 text-[#00E5CC]';
  if (['blocked', 'rejected'].includes(v)) return 'bg-red-500/10 text-red-400';
  if (['in_progress', 'awaiting_approval', 'deploying'].includes(v)) return 'bg-[#F59E0B]/10 text-[#F59E0B]';
  return 'bg-[#3B82F6]/10 text-[#60A5FA]';
}

// 우선순위 1 긴급 ~ 5 낮음 — 1 적색 / 2 앰버 / 3 청색 / 4·5 회색
function priorityTone(priority: number | null | undefined): string {
  if (priority === 1) return 'bg-red-500/10 text-red-400 border border-red-500/30';
  if (priority === 2) return 'bg-[#F59E0B]/10 text-[#F59E0B] border border-[#F59E0B]/30';
  if (priority === 3) return 'bg-[#3B82F6]/10 text-[#60A5FA] border border-[#3B82F6]/30';
  return 'bg-[#1E2530] text-[#8B9BB4] border border-[#2A3441]';
}

function riskTone(risk: string | null | undefined): string {
  const v = (risk || '').toLowerCase();
  if (v === 'high') return 'bg-red-500/10 text-red-400';
  if (v === 'low') return 'bg-[#00E5CC]/10 text-[#00E5CC]';
  return 'bg-[#1E2530] text-[#8B9BB4]';
}

// 감사 이벤트 색 — 실패·막힘·기각 적색, 검증·종료·배포 완료 teal, 선점·시작 앰버, 그 외 회색
function eventTone(event: string | null | undefined): string {
  const v = (event || '').toLowerCase();
  if (['fail', 'block', 'reject', 'error', 'rollback'].some(k => v.includes(k))) return 'bg-red-500/10 text-red-400';
  if (['verified', 'closed', 'deployed', 'done', 'success'].some(k => v.includes(k))) return 'bg-[#00E5CC]/10 text-[#00E5CC]';
  if (['claim', 'start', 'deploying', 'retry'].some(k => v.includes(k))) return 'bg-[#F59E0B]/10 text-[#F59E0B]';
  return 'bg-[#1E2530] text-[#8B9BB4]';
}

function fmtDate(value: string | null | undefined): string | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString('ko-KR');
}

function fmtUsd(value: number | string | null | undefined): string {
  const n = typeof value === 'number' ? value : Number(value ?? 0);
  return `$${(Number.isFinite(n) ? n : 0).toFixed(2)}`;
}

function toInt(value: number | string | null | undefined, fallback = 0): number {
  const n = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function Chip({ text, tone = 'bg-[#1E2530] text-[#8B9BB4]', title }: { text: string; tone?: string; title?: string }) {
  return <span className={`text-[10px] px-1.5 py-0.5 rounded ${tone}`} title={title}>{text}</span>;
}

function StatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return <Dash />;
  return (
    <span className={`text-[10px] px-2 py-0.5 rounded whitespace-nowrap ${statusTone(status)}`} title={status}>
      {STATUS_LABELS[status] ?? status}
    </span>
  );
}

// ─── 정책 카드 (정지 스위치 + 일일 상한) ────────────────────────────────────────────

function PolicyCard({ policy, runbooks, busy, onToggle, activity }: {
  policy: AgentPolicy | null | undefined;
  runbooks: string[];
  busy: boolean;
  onToggle: () => void;
  activity?: { last_claimed_at: string | null; waiting: number };
}) {
  const enabled = policy?.enabled === true;
  // 스위치 '허용' ≠ 실제 처리. 24시간 넘게 선점이 없고 대기 과제가 있으면 처리 주체(디스패처)가 멈춘 것으로 표시한다.
  const lastClaim = activity?.last_claimed_at ? new Date(activity.last_claimed_at) : null;
  const idle = !lastClaim || (Date.now() - lastClaim.getTime()) > 24 * 3600 * 1000;
  const stalled = enabled && idle && (activity?.waiting ?? 0) > 0;
  const frame = policy
    ? (enabled ? 'border-[#1E2530]' : 'border-red-500/40 bg-red-500/5')
    : 'border-[#1E2530]';
  return (
    <div className={`bg-[#161B27] rounded-2xl border p-5 ${frame}`}>
      <div className="flex flex-wrap items-center gap-4">
        <div className="flex items-center gap-3">
          <button
            type="button"
            role="switch"
            aria-checked={enabled}
            aria-label={enabled ? '에이전트 팀 정지' : '에이전트 팀 재개'}
            disabled={busy || !policy}
            onClick={onToggle}
            className={`relative w-11 h-6 rounded-full transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed ${enabled ? 'bg-[#00E5CC]' : 'bg-red-500/70'}`}
          >
            <span className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-[#0D1117] transition-transform ${enabled ? 'translate-x-5' : 'translate-x-0'}`}></span>
          </button>
          <div>
            <p className="text-[#4A5568] text-[10px]">정지 스위치</p>
            {!policy ? (
              <p className="text-[#8B9BB4] text-sm">정책 정보 없음</p>
            ) : enabled && stalled ? (
              <p className="text-sm font-semibold text-[#F59E0B]" title="정책상 선점은 허용돼 있지만 최근 24시간 동안 과제를 가져간 처리 주체가 없습니다">
                <i className="ri-pause-circle-line mr-1"></i>선점 허용 · 처리 멈춤
              </p>
            ) : enabled ? (
              <p className="text-sm font-semibold text-[#00E5CC]"><i className="ri-play-circle-line mr-1"></i>에이전트 팀 가동 중</p>
            ) : (
              <p className="text-sm font-bold text-red-400"><i className="ri-stop-circle-line mr-1"></i>에이전트 팀 정지 중</p>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2">
          <span className="w-8 h-8 flex items-center justify-center rounded-lg bg-[#F59E0B]/10 text-[#F59E0B]"><i className="ri-time-line"></i></span>
          <div>
            <p className="text-[#4A5568] text-[10px]">마지막 과제 선점</p>
            <p className="text-white text-sm font-semibold">{lastClaim ? lastClaim.toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' }) : '기록 없음'}{activity ? ` · 대기 ${activity.waiting}건` : ''}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="w-8 h-8 flex items-center justify-center rounded-lg bg-[#3B82F6]/10 text-[#60A5FA]"><i className="ri-task-line"></i></span>
          <div>
            <p className="text-[#4A5568] text-[10px]">일일 작업 상한</p>
            <p className="text-white text-sm font-semibold">{policy?.max_tasks_per_day != null ? `${toInt(policy.max_tasks_per_day)}건` : <Dash />}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="w-8 h-8 flex items-center justify-center rounded-lg bg-[#7C3AED]/10 text-[#A78BFA]"><i className="ri-money-dollar-circle-line"></i></span>
          <div>
            <p className="text-[#4A5568] text-[10px]">일일 LLM 비용 상한</p>
            <p className="text-white text-sm font-semibold">{policy?.max_cost_usd_per_day != null ? fmtUsd(policy.max_cost_usd_per_day) : <Dash />}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="w-8 h-8 flex items-center justify-center rounded-lg bg-[#00E5CC]/10 text-[#00E5CC]"><i className="ri-rocket-2-line"></i></span>
          <div>
            <p className="text-[#4A5568] text-[10px]">일일 자동 배포 상한</p>
            <p className="text-white text-sm font-semibold">{policy?.max_deploys_per_day != null ? `${toInt(policy.max_deploys_per_day)}회` : <Dash />}</p>
          </div>
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-2">
          {policy && !enabled && (
            <span className="text-[10px] px-3 py-1.5 rounded-full bg-red-500/10 text-red-400 border border-red-500/30 whitespace-nowrap">
              <i className="ri-alert-line mr-1"></i>새 과제 선점 중단 — 기존 스케줄러(수집·발송)는 계속 동작
            </span>
          )}
          <span className="text-[10px] px-3 py-1.5 rounded-full bg-[#1E2530] text-[#8B9BB4] whitespace-nowrap">
            <i className="ri-information-line mr-1"></i>상한 초과 시 새 선점을 멈추고 대기열에 쌓임
          </span>
        </div>
      </div>

      {runbooks.length > 0 && (
        <div className="mt-4 pt-4 border-t border-[#1E2530] flex flex-wrap items-center gap-1.5">
          <span className="text-[#4A5568] text-[10px] mr-1">등록 런북 {runbooks.length}개</span>
          {runbooks.map(r => <Chip key={r} text={r} tone="bg-[#00E5CC]/10 text-[#00E5CC]" />)}
        </div>
      )}
    </div>
  );
}

// ─── 과제 테이블 ─────────────────────────────────────────────────────────────────

function TaskRow({ task, selected, onSelect }: { task: AgentTask; selected: boolean; onSelect: (id: number) => void }) {
  const priority = toInt(task.priority, 3);
  const attempts = toInt(task.attempts, 0);
  const kind = task.kind ?? '';
  const source = task.source ?? '';
  return (
    <tr
      onClick={() => onSelect(task.id)}
      className={`border-b border-[#1E2530]/50 last:border-b-0 align-top cursor-pointer ${selected ? 'bg-[#00E5CC]/5' : 'hover:bg-[#1E2530]/30'}`}
    >
      <td className="py-2.5 pr-3 whitespace-nowrap">
        <span className={`text-[10px] px-2 py-0.5 rounded ${priorityTone(priority)}`} title={`우선순위 ${priority} (1 긴급 ~ 5 낮음)`}>P{priority}</span>
      </td>
      <td className="py-2.5 pr-3 whitespace-nowrap">
        {kind ? <Chip text={KIND_LABELS[kind] ?? kind} title={kind} /> : <Dash />}
      </td>
      <td className="py-2.5 pr-3 max-w-[320px]">
        <span className="block text-white text-xs truncate" title={task.title ?? ''}>{task.title || <Dash />}</span>
        <span className="text-[#4A5568] text-[10px] font-mono">#{task.id}</span>
        {task.risk && <span className={`ml-1 text-[10px] px-1.5 py-0.5 rounded ${riskTone(task.risk)}`}>risk {task.risk}</span>}
      </td>
      <td className="py-2.5 pr-3 whitespace-nowrap"><StatusBadge status={task.status} /></td>
      <td className="py-2.5 pr-3 whitespace-nowrap"><OwnerBadge task={task} /></td>
      <td className="py-2.5 pr-3 text-[#8B9BB4] text-xs whitespace-nowrap">
        {task.claimed_by ? (
          <>
            <span className="text-white">{task.claimed_by}</span>
            {task.claimed_at && <p className="text-[#4A5568] text-[10px] mt-0.5">{fmtDate(task.claimed_at)}</p>}
          </>
        ) : <Dash />}
      </td>
      <td className="py-2.5 pr-3 text-xs whitespace-nowrap">
        <span className={attempts >= 2 ? 'text-[#F59E0B]' : 'text-[#8B9BB4]'}>{attempts}</span>
      </td>
      <td className="py-2.5 pr-3 text-[#8B9BB4] text-xs whitespace-nowrap font-mono">{fmtUsd(task.cost_usd)}</td>
      <td className="py-2.5 pr-3 text-xs max-w-[220px]">
        {source ? <Chip text={SOURCE_LABELS[source] ?? source} title={source} /> : <Dash />}
        {task.source_ref && (
          <p className="text-[#4A5568] text-[10px] font-mono mt-0.5 truncate" title={task.source_ref}>{task.source_ref}</p>
        )}
      </td>
      <td className="py-2.5 text-[#8B9BB4] text-xs whitespace-nowrap">{fmtDate(task.updated_at) ?? <Dash />}</td>
    </tr>
  );
}

// ─── 상세 패널 (detail 전문 + 감사 로그) ────────────────────────────────────────────

function MetaItem({ label, value, mono = false }: { label: string; value: string | number | null | undefined; mono?: boolean }) {
  const shown = value === null || value === undefined || value === '' ? null : String(value);
  return (
    <div>
      <p className="text-[#4A5568] text-[10px]">{label}</p>
      <p className={`text-xs text-[#8B9BB4] break-all ${mono ? 'font-mono' : ''}`}>{shown ?? <Dash />}</p>
    </div>
  );
}

function sortEvents(events: AgentTaskEvent[]): AgentTaskEvent[] {
  return [...events].sort((a, b) => {
    const ta = a.created_at ?? '';
    const tb = b.created_at ?? '';
    if (ta !== tb) return ta < tb ? -1 : 1;
    return toInt(a.id) - toInt(b.id);
  });
}

function DetailPanel({ selectedId, detail, loading, error, onClose, onRetry }: {
  selectedId: number | null;
  detail: AgentTaskDetailResponse | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
  onRetry: () => void;
}) {
  if (selectedId == null) {
    return (
      <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-6 text-center text-[#4A5568] text-sm">
        <span className="w-12 h-12 flex items-center justify-center mx-auto mb-3"><i className="ri-file-search-line text-4xl"></i></span>
        행을 클릭하면 근거 전문과 감사 로그가 여기에 표시됩니다.
      </div>
    );
  }
  const task = detail?.task ?? null;
  const events = sortEvents(Array.isArray(detail?.events) ? detail.events : []);
  return (
    <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5 space-y-4 lg:sticky lg:top-6 max-h-[calc(100vh-3rem)] overflow-y-auto">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[#4A5568] text-[10px] font-mono">과제 #{selectedId}</p>
          <h3 className="text-white font-bold text-sm break-words">{task?.title || (loading ? '불러오는 중…' : '제목 없음')}</h3>
        </div>
        <button onClick={onClose} className="text-[#8B9BB4] hover:text-white cursor-pointer flex-shrink-0" aria-label="닫기">
          <i className="ri-close-line text-lg"></i>
        </button>
      </div>

      {loading && (
        <div className="text-center py-8 text-sm text-[#8B9BB4]">
          <i className="ri-loader-4-line animate-spin mr-2"></i>상세 로드 중…
        </div>
      )}
      {!loading && error && (
        <div className="text-center py-8">
          <p className="text-sm text-red-400">{error}</p>
          <button onClick={onRetry} className="mt-3 text-sm cursor-pointer hover:underline text-[#00E5CC]">다시 시도</button>
        </div>
      )}

      {!loading && !error && task && (
        <>
          <div className="flex flex-wrap items-center gap-1.5">
            <StatusBadge status={task.status} />
            <span className={`text-[10px] px-2 py-0.5 rounded ${priorityTone(toInt(task.priority, 3))}`}>P{toInt(task.priority, 3)}</span>
            {task.kind && <Chip text={KIND_LABELS[task.kind] ?? task.kind} title={task.kind} />}
            {task.risk && <Chip text={`risk ${task.risk}`} tone={riskTone(task.risk)} />}
          </div>

          <div className="grid grid-cols-2 gap-x-4 gap-y-3 bg-[#0D1117] border border-[#1E2530] rounded-xl p-3">
            <MetaItem label="근거 소스" value={task.source ? (SOURCE_LABELS[task.source] ?? task.source) : null} />
            <MetaItem label="근거 참조" value={task.source_ref} mono />
            <MetaItem label="담당" value={OWNER_LABELS[ownerOf(task)] ?? '미분류'} />
            <MetaItem label="선점자" value={task.claimed_by} />
            <MetaItem label="선점 시각" value={fmtDate(task.claimed_at)} />
            <MetaItem label="시도" value={toInt(task.attempts, 0)} />
            <MetaItem label="비용" value={fmtUsd(task.cost_usd)} mono />
            <MetaItem label="브랜치" value={task.branch} mono />
            <MetaItem label="커밋" value={task.commit_ref} mono />
            <MetaItem label="배포 버전" value={task.deploy_version} mono />
            <MetaItem label="생성" value={fmtDate(task.created_at)} />
            <MetaItem label="최근 갱신" value={fmtDate(task.updated_at)} />
          </div>

          {task.resolution && (
            <div className="bg-[#0D1117] border border-[#1E2530] rounded-xl p-3">
              <p className="text-[#4A5568] text-[10px] mb-1">처리 결과 / 기각 사유</p>
              <p className="text-xs text-[#8B9BB4] whitespace-pre-wrap break-words">{task.resolution}</p>
            </div>
          )}

          <div>
            <p className="text-[#4A5568] text-[10px] mb-1">근거·재현 방법 (detail)</p>
            <pre className="bg-[#0D1117] border border-[#1E2530] rounded-xl p-3 text-xs text-[#8B9BB4] whitespace-pre-wrap break-words font-sans max-h-[40vh] overflow-y-auto">
              {task.detail || '(내용 없음)'}
            </pre>
          </div>

          <div>
            <div className="flex items-center justify-between mb-2">
              <p className="text-white text-xs font-bold">감사 로그</p>
              <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-[#1E2530] text-[#8B9BB4]">{events.length}</span>
            </div>
            {events.length === 0 ? (
              <p className="text-[#4A5568] text-xs text-center py-4">기록된 이벤트가 없습니다.</p>
            ) : (
              <ol className="space-y-2">
                {events.map((ev, idx) => (
                  <li key={ev.id ?? idx} className="bg-[#0D1117] border border-[#1E2530] rounded-xl p-3">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="text-[#4A5568] text-[10px] whitespace-nowrap">{fmtDate(ev.created_at) ?? '—'}</span>
                      <span className="text-white text-[10px] font-mono">{ev.actor || '—'}</span>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded ${eventTone(ev.event)}`}>{ev.event || '—'}</span>
                    </div>
                    {ev.detail && <p className="text-xs text-[#8B9BB4] whitespace-pre-wrap break-words mt-1.5">{ev.detail}</p>}
                  </li>
                ))}
              </ol>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// ─── 페이지 ──────────────────────────────────────────────────────────────────────

const ACTIVE_STATUSES = new Set(['in_progress', 'deploying']);
const WAITING_STATUSES = new Set(['awaiting_approval']);
const HUMAN_STATUSES = new Set(['blocked']);

export default function AdminAgentTasksPage() {
  const navigate = useNavigate();
  const [authChecked, setAuthChecked] = useState(false);
  const [data, setData] = useState<AgentTasksResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [policyBusy, setPolicyBusy] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [ownerFilter, setOwnerFilter] = useState<OwnerFilter>('all');
  const [detail, setDetail] = useState<AgentTaskDetailResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const me = await fetchMe();
        if (!me || me.role !== 'admin') {
          navigate('/', { replace: true });
          return;
        }
        setAuthChecked(true);
      } catch {
        navigate('/login', { replace: true });
      }
    })();
  }, [navigate]);

  const reload = async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchAgentTasks());
    } catch (e) {
      setError(e instanceof Error ? e.message : '운영 원장 조회 실패');
    } finally {
      setLoading(false);
    }
  };

  const loadDetail = async (id: number) => {
    setDetailLoading(true);
    setDetailError(null);
    try {
      setDetail(await fetchAgentTaskDetail(id));
    } catch (e) {
      setDetail(null);
      setDetailError(e instanceof Error ? e.message : '상세 조회 실패');
    } finally {
      setDetailLoading(false);
    }
  };

  const selectTask = (id: number) => {
    setSelectedId(id);
    setDetail(null);
    loadDetail(id);
  };

  const togglePolicy = async () => {
    const policy = data?.policy;
    if (!policy) return;
    const next = !(policy.enabled === true);
    const msg = next
      ? '에이전트 팀을 재개할까요? 대기 중인 과제 선점이 다시 시작됩니다.'
      : '에이전트 팀을 정지할까요? 새 과제 선점이 멈춥니다 (수집·발송 스케줄러는 계속 동작).';
    if (!window.confirm(msg)) return;
    setPolicyBusy(true);
    try {
      await updateAgentPolicy({ enabled: next });
      await reload();
    } catch (e) {
      alert(e instanceof Error ? e.message : '정책 변경 실패');
      // 서버 상태는 바뀌었을 수 있다 — 실패 경로에서도 다시 읽는다 (reload 는 자체적으로 오류를 삼킨다).
      await reload();
    } finally {
      setPolicyBusy(false);
    }
  };

  useEffect(() => {
    if (authChecked) reload();
  }, [authChecked]);

  if (!authChecked) {
    return (
      <div className="min-h-screen flex items-center justify-center text-[#8B9BB4] text-sm">
        <i className="ri-loader-4-line animate-spin mr-2"></i>권한 확인 중…
      </div>
    );
  }

  const allTasks: AgentTask[] = Array.isArray(data?.tasks) ? data.tasks : [];
  const ownerCounts = allTasks.reduce<Record<string, number>>((acc, t) => {
    const o = ownerOf(t);
    acc[o] = (acc[o] ?? 0) + 1;
    return acc;
  }, {});
  const tasks: AgentTask[] = ownerFilter === 'all' ? allTasks : allTasks.filter(t => ownerOf(t) === ownerFilter);
  const waitingByOwner = data?.activity?.waiting_by_owner ?? {};
  const runbooks: string[] = Array.isArray(data?.runbooks) ? data.runbooks.filter((r): r is string => typeof r === 'string') : [];
  const policy = data?.policy ?? null;
  const statusOf = (t: AgentTask) => (t.status || '').toLowerCase();
  const activeCount = tasks.filter(t => ACTIVE_STATUSES.has(statusOf(t))).length;
  const waitingCount = tasks.filter(t => WAITING_STATUSES.has(statusOf(t))).length;
  const humanCount = tasks.filter(t => HUMAN_STATUSES.has(statusOf(t))).length;
  const totalCost = tasks.reduce((sum, t) => sum + (Number.isFinite(Number(t.cost_usd)) ? Number(t.cost_usd) : 0), 0);

  return (
    <div className="min-h-screen bg-[#0D1117] text-white">
      <div className="px-8 pt-8 pb-6 border-b border-[#1E2530]">
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="w-5 h-5 flex items-center justify-center"><i className="ri-robot-line text-[#00E5CC]"></i></span>
              <h1 className="text-2xl font-bold text-white">에이전트 팀 — 운영 원장</h1>
            </div>
            <p className="text-[#8B9BB4] text-sm max-w-3xl">
              VPS 상주 에이전트 팀이 처리하는 과제(agent_task)의 상태·담당·비용·근거와 append-only 감사 로그 (Admin 전용).
              정지 스위치를 끄면 새 과제 선점이 멈추고, 기존 수집·발송 스케줄러는 계속 돕니다.
            </p>
          </div>
          <button onClick={reload} className="text-[#8B9BB4] text-xs hover:text-white cursor-pointer flex items-center gap-1 whitespace-nowrap">
            <i className="ri-refresh-line"></i>새로고침
          </button>
        </div>
      </div>

      <div className="px-8 py-6 space-y-5">
        {loading && (
          <div className="text-center py-16 text-sm text-[#8B9BB4]">
            <i className="ri-loader-4-line animate-spin mr-2"></i>운영 원장 로드 중…
          </div>
        )}
        {!loading && error && (
          <div className="text-center py-16">
            <span className="w-12 h-12 flex items-center justify-center mx-auto mb-3"><i className="ri-error-warning-line text-4xl text-red-400"></i></span>
            <p className="text-sm text-red-400">{error}</p>
            <button onClick={reload} className="mt-4 text-sm cursor-pointer hover:underline text-[#00E5CC]">다시 시도</button>
          </div>
        )}

        {!loading && !error && data && (
          <>
            <PolicyCard policy={policy} runbooks={runbooks} busy={policyBusy} onToggle={togglePolicy} activity={data?.activity} />

            {/* Summary */}
            <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5 flex flex-wrap items-center gap-4">
              <div className="flex items-center gap-2">
                <span className="w-8 h-8 flex items-center justify-center rounded-lg bg-[#3B82F6]/10 text-[#60A5FA]"><i className="ri-list-check-2"></i></span>
                <div>
                  <p className="text-[#4A5568] text-[10px]">과제 수</p>
                  <p className="text-white text-sm font-semibold">{tasks.length}건</p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <span className="w-8 h-8 flex items-center justify-center rounded-lg bg-[#F59E0B]/10 text-[#F59E0B]"><i className="ri-loader-2-line"></i></span>
                <div>
                  <p className="text-[#4A5568] text-[10px]">진행·배포 중</p>
                  <p className="text-white text-sm font-semibold">{activeCount}건</p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <span className="w-8 h-8 flex items-center justify-center rounded-lg bg-[#F59E0B]/10 text-[#F59E0B]"><i className="ri-user-voice-line"></i></span>
                <div>
                  <p className="text-[#4A5568] text-[10px]">승인 대기</p>
                  <p className="text-white text-sm font-semibold">{waitingCount}건</p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <span className="w-8 h-8 flex items-center justify-center rounded-lg bg-red-500/10 text-red-400"><i className="ri-hand"></i></span>
                <div>
                  <p className="text-[#4A5568] text-[10px]">막힘 (사람 필요)</p>
                  <p className={`text-sm font-semibold ${humanCount > 0 ? 'text-red-400' : 'text-white'}`}>{humanCount}건</p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <span className="w-8 h-8 flex items-center justify-center rounded-lg bg-[#7C3AED]/10 text-[#A78BFA]"><i className="ri-money-dollar-circle-line"></i></span>
                <div>
                  <p className="text-[#4A5568] text-[10px]">누적 비용 (표시 과제)</p>
                  <p className="text-white text-sm font-semibold font-mono">{fmtUsd(totalCost)}</p>
                </div>
              </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_400px] gap-5 items-start">
              {/* Task table */}
              <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-6 min-w-0">
                <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
                  <h2 className="text-white font-bold text-base">과제 원장</h2>
                  <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="담당 필터">
                    {OWNER_FILTERS.filter(o => o !== 'unknown' || (ownerCounts.unknown ?? 0) > 0).map(o => {
                      const active = ownerFilter === o;
                      const count = o === 'all' ? allTasks.length : (ownerCounts[o] ?? 0);
                      const waiting = o === 'all' || o === 'unknown' ? null : (waitingByOwner as Record<string, number | undefined>)[o];
                      const label = o === 'all' ? '전체' : (OWNER_LABELS[o] ?? '미분류');
                      return (
                        <button
                          key={o}
                          type="button"
                          aria-pressed={active}
                          onClick={() => setOwnerFilter(o)}
                          title={o === 'all' ? '모든 담당' : (OWNER_HINTS[o] ?? '담당 미분류(구버전 서버)')}
                          className={`text-[11px] px-2.5 py-1 rounded-full border cursor-pointer whitespace-nowrap ${active ? 'border-[#00E5CC] text-[#00E5CC] bg-[#00E5CC]/10' : 'border-[#2A3441] text-[#8B9BB4] hover:text-white'}`}
                        >
                          {label} {count}{waiting != null && waiting > 0 ? ` · 대기 ${waiting}` : ''}
                        </button>
                      );
                    })}
                  </div>
                </div>
                {tasks.length === 0 ? (
                  <p className="text-[#4A5568] text-sm">
                    {allTasks.length === 0
                      ? '아직 과제가 없습니다. 헬스 체크·Quality Guard 등 신호가 들어오면 여기에 쌓입니다.'
                      : '이 담당의 과제가 없습니다.'}
                  </p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="text-[#8B9BB4] text-xs border-b border-[#1E2530]">
                          <th className="text-left py-2 pr-3">우선순위</th>
                          <th className="text-left py-2 pr-3">종류</th>
                          <th className="text-left py-2 pr-3">제목</th>
                          <th className="text-left py-2 pr-3">상태</th>
                          <th className="text-left py-2 pr-3">담당</th>
                          <th className="text-left py-2 pr-3">선점</th>
                          <th className="text-left py-2 pr-3">시도</th>
                          <th className="text-left py-2 pr-3">비용</th>
                          <th className="text-left py-2 pr-3">근거</th>
                          <th className="text-left py-2">최근 갱신</th>
                        </tr>
                      </thead>
                      <tbody>
                        {tasks.map((t, idx) => (
                          <TaskRow key={t.id ?? idx} task={t} selected={selectedId === t.id} onSelect={selectTask} />
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              {/* Detail side panel */}
              <DetailPanel
                selectedId={selectedId}
                detail={detail}
                loading={detailLoading}
                error={detailError}
                onClose={() => { setSelectedId(null); setDetail(null); setDetailError(null); }}
                onRetry={() => { if (selectedId != null) loadDetail(selectedId); }}
              />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
