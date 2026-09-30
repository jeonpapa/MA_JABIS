import { api } from './client';

// 에이전트 팀 운영 원장 계약 — agents/agent_tasks/** (P1) / api/server.py
//   GET  /api/admin/agent-tasks        → { tasks, policy, runbooks }
//   GET  /api/admin/agent-tasks/<id>   → { task, events }
//   POST /api/admin/agent-policy       → { policy }   (body = 부분 정책)
// 설계: docs/superpowers/specs/2026-09-30-agent-team-full-service-design.md §5·§7·§8
// 주의: 서버가 오래된 행/부재 필드를 돌려줄 수 있다 — 소비 측(page.tsx)은 모든 필드를 방어적으로 읽는다.

export interface AgentTask {
  id: number;
  kind: string;            // ops_incident | user_request | improvement | release
  source: string;          // P1 실제 값(agents/agent_tasks/signals.py): scheduler_job | daily_mailing_health
                           // 설계 §5 예정 값(P2~P3): health_check | quality_guard | rule_compliance | service_request | mail_stats | manual
  source_ref: string | null;
  title: string;
  detail: string;
  priority: number;        // 1 긴급 ~ 5 낮음
  risk: string | null;     // low | high
  status: string;          // new|triaged|in_progress|verified|awaiting_approval|deploying|deployed|closed|blocked|rejected
  claimed_by: string | null;
  claimed_at: string | null;
  branch: string | null;
  commit_ref: string | null;
  deploy_version: string | null;
  attempts: number;
  cost_usd: number;
  resolution: string | null;
  created_at: string;
  updated_at: string;
}

export interface AgentPolicy {
  enabled: boolean;
  max_tasks_per_day: number;
  max_cost_usd_per_day: number;
  max_deploys_per_day: number;
}

export interface AgentTaskEvent {
  id: number;
  task_id: number;
  actor: string;
  event: string;
  detail: string | null;
  created_at: string;
}

export interface AgentTasksResponse {
  tasks: AgentTask[];
  policy: AgentPolicy;
  runbooks: string[];
}

export interface AgentTaskDetailResponse {
  task: AgentTask;
  events: AgentTaskEvent[];
}

export async function fetchAgentTasks(): Promise<AgentTasksResponse> {
  return api.get<AgentTasksResponse>('/api/admin/agent-tasks');
}

export async function fetchAgentTaskDetail(id: number): Promise<AgentTaskDetailResponse> {
  return api.get<AgentTaskDetailResponse>(`/api/admin/agent-tasks/${id}`);
}

export async function updateAgentPolicy(patch: Partial<AgentPolicy>): Promise<{ policy: AgentPolicy }> {
  return api.post<{ policy: AgentPolicy }>('/api/admin/agent-policy', patch);
}
