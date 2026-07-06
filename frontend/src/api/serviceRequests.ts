import { api } from './client';

// ── 서비스 보완/개선 요청 (Service Request) API 모듈 ─────────────────────────
// 계약: POST /api/service-requests, GET /mine, GET /<id>,
//       GET/PATCH /api/admin/service-requests..., claude-package/confirm/send-to-claude
// 방어적 타이핑: 구버전 데이터에는 필드가 없을 수 있어 대부분 optional/nullable.

export type SRRequestType = 'bug' | 'improvement' | 'feature' | 'data' | 'other';
export type SRPriority = 'low' | 'medium' | 'high' | 'urgent';
export type SRStatus =
  | 'open'
  | 'in_review'
  | 'packaged'
  | 'confirmed'
  | 'sent'
  | 'rejected'
  | 'done';
export type SRPackageStatus = 'none' | 'draft' | 'final';

export interface ChecklistState {
  scope_clear: boolean;
  context_redacted: boolean;
  no_secrets: boolean;
  expected_outcome_defined: boolean;
  no_deploy_ack: boolean;
}

export const EMPTY_CHECKLIST: ChecklistState = {
  scope_clear: false,
  context_redacted: false,
  no_secrets: false,
  expected_outcome_defined: false,
  no_deploy_ack: false,
};

export interface SR {
  id: number;
  owner_email?: string | null;
  page_path?: string | null;
  page_label?: string | null;
  source_url?: string | null;
  request_type?: SRRequestType | string | null;
  priority?: SRPriority | string | null;
  title?: string | null;
  body?: string | null;
  expected_outcome?: string | null;
  context?: Record<string, unknown> | null;
  status?: SRStatus | string | null;
  admin_note?: string | null;
  package_markdown?: string | null;
  package_status?: SRPackageStatus | string | null;
  checklist?: Partial<ChecklistState> | null;
  confirmed_at?: string | null;
  sent_at?: string | null;
  sent_markdown?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface SREvent {
  id: number;
  request_id?: number | null;
  actor_email?: string | null;
  event_type?: string | null;
  from_status?: string | null;
  to_status?: string | null;
  note?: string | null;
  created_at?: string | null;
}

export interface ServiceRequestInput {
  title: string;
  body?: string;
  expected_outcome?: string;
  request_type?: SRRequestType;
  priority?: SRPriority;
  page_path?: string;
  page_label?: string;
  source_url?: string;
  context?: Record<string, unknown>;
}

export interface AdminPatchInput {
  status?: SRStatus;
  priority?: SRPriority;
  request_type?: SRRequestType;
  admin_note?: string;
}

export interface AdminListFilters {
  status?: string;
  priority?: string;
  type?: string;
  limit?: number;
}

export type PackageMode = 'generate' | 'save_draft' | 'save_final';

// ── 한글 라벨 (UI 공용) ──────────────────────────────────────────────────────

export const REQUEST_TYPE_LABELS: Record<string, string> = {
  bug: '버그',
  improvement: '개선',
  feature: '신규 기능',
  data: '데이터',
  other: '기타',
};

export const PRIORITY_LABELS: Record<string, string> = {
  low: '낮음',
  medium: '보통',
  high: '높음',
  urgent: '긴급',
};

export const STATUS_LABELS: Record<string, string> = {
  open: '접수됨',
  in_review: '검토 중',
  packaged: '패키지 작성',
  confirmed: '확인 완료',
  sent: '전달됨',
  rejected: '반려',
  done: '완료',
};

export const EVENT_TYPE_LABELS: Record<string, string> = {
  create: '요청 접수',
  update: '내용 수정',
  package: 'Claude 패키지',
  confirm: '최종 확인',
  send: 'Claude 전달',
  reject: '반려',
};

// ── API 함수 ────────────────────────────────────────────────────────────────

export async function submitServiceRequest(input: ServiceRequestInput): Promise<SR> {
  const r = await api.post<{ item: SR }>('/api/service-requests', input);
  return r.item;
}

export async function fetchMyRequests(): Promise<SR[]> {
  const r = await api.get<{ items?: SR[] }>('/api/service-requests/mine');
  return r?.items ?? [];
}

export async function fetchServiceRequest(
  id: number,
): Promise<{ item: SR; events: SREvent[] }> {
  const r = await api.get<{ item: SR; events?: SREvent[] }>(`/api/service-requests/${id}`);
  return { item: r.item, events: r?.events ?? [] };
}

export async function adminListRequests(filters: AdminListFilters = {}): Promise<SR[]> {
  const qs = new URLSearchParams();
  if (filters.status) qs.set('status', filters.status);
  if (filters.priority) qs.set('priority', filters.priority);
  if (filters.type) qs.set('type', filters.type);
  if (filters.limit != null) qs.set('limit', String(filters.limit));
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  const r = await api.get<{ items?: SR[] }>(`/api/admin/service-requests${suffix}`);
  return r?.items ?? [];
}

export async function adminPatchRequest(id: number, patch: AdminPatchInput): Promise<SR> {
  const r = await api.patch<{ item: SR }>(`/api/admin/service-requests/${id}`, patch);
  return r.item;
}

export async function adminPackage(
  id: number,
  opts: { mode: PackageMode; markdown?: string },
): Promise<{ item: SR; markdown: string }> {
  const r = await api.post<{ item: SR; markdown?: string }>(
    `/api/admin/service-requests/${id}/claude-package`,
    opts,
  );
  return { item: r.item, markdown: r?.markdown ?? '' };
}

export async function adminConfirm(id: number, checklist: ChecklistState): Promise<SR> {
  const r = await api.post<{ item: SR }>(`/api/admin/service-requests/${id}/confirm`, {
    checklist,
  });
  return r.item;
}

export async function adminSend(id: number): Promise<{ item: SR; markdown: string }> {
  const r = await api.post<{ item: SR; markdown?: string }>(
    `/api/admin/service-requests/${id}/send-to-claude`,
  );
  return { item: r.item, markdown: r?.markdown ?? '' };
}
