import { api, ApiError, getToken } from './client';

/** 분석 요청(A안) — 질문을 남기면 Aside 워커가 처리하고 결과를 올린다. 서버는 분석을 실행하지 않는다. */
export type AnalysisStatus = 'queued' | 'processing' | 'needs_info' | 'completed' | 'failed' | 'cancelled';

export interface AnalysisFile {
  id: number;
  filename: string;
  content_type: string;
  size: number;
  created_at: string;
}

export type AnalysisSource = string | Record<string, string | null>;

export interface AnalysisRequest {
  id: string;
  requester: string;
  question: string;
  context_url?: string | null;
  share_team: boolean;
  status: AnalysisStatus;
  status_label: string;
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
  clarification?: string | null;
  reply?: string | null;
  result_summary?: string | null;
  result_method?: string | null;
  result_assumptions: AnalysisSource[];
  result_sources: AnalysisSource[];
  result_limitations?: string | null;
  result_data_as_of?: string | null;
  failure_reason?: string | null;
  files: AnalysisFile[];
  events?: { at: string; actor: string; event: string; detail?: string | null }[];
}

export const createAnalysisRequest = (question: string, shareTeam: boolean, contextUrl?: string) =>
  api.post<AnalysisRequest>('/api/analysis-requests', { question, share_team: shareTeam, context_url: contextUrl || undefined });

export const listAnalysisRequests = () =>
  api.get<{ items: AnalysisRequest[] }>('/api/analysis-requests').then(r => r.items);

export const getAnalysisRequest = (id: string) => api.get<AnalysisRequest>(`/api/analysis-requests/${encodeURIComponent(id)}`);

export const cancelAnalysisRequest = (id: string) =>
  api.post<AnalysisRequest>(`/api/analysis-requests/${encodeURIComponent(id)}/cancel`);

export const replyAnalysisRequest = (id: string, reply: string) =>
  api.post<AnalysisRequest>(`/api/analysis-requests/${encodeURIComponent(id)}/reply`, { reply });

export async function downloadAnalysisFile(id: string, file: AnalysisFile): Promise<void> {
  const res = await fetch(`/api/analysis-requests/${encodeURIComponent(id)}/files/${file.id}`, {
    headers: { Authorization: `Bearer ${getToken() ?? ''}` },
  });
  if (!res.ok) throw new ApiError(res.status === 404 ? '파일을 찾을 수 없습니다.' : `다운로드 실패 (HTTP ${res.status})`, res.status);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  try {
    a.href = url;
    a.download = file.filename;
    document.body.appendChild(a);
    a.click();
  } finally {
    a.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}

export function sourceText(s: AnalysisSource): string {
  if (typeof s === 'string') return s;
  return Object.entries(s)
    .filter(([, v]) => v)
    .map(([k, v]) => (k === 'name' ? v : `${k}: ${v}`))
    .join(' · ');
}
