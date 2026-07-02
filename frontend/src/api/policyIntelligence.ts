import { api, getToken } from './client';

export interface PolicyOverview {
  created_at: string | null;
  source_batch_id: string;
  event_count: number;
  topic_count: number;
  document_count: number;
  high_impact_count: number;
  latest_event_date: string | null;
  severity_counts: Record<string, number>;
  excluded_general_media_event_count?: number;
  report_available: boolean;
}

export interface PolicyTopic {
  topic: string;
  event_count: number;
  latest_date: string | null;
  latest_subject: string | null;
  severity: string;
  status: string;
  next_action: string;
}

export interface PolicyChangeRecord {
  change_id: string;
  topic_id: string;
  topic_name: string;
  event_id: string;
  date: string | null;
  change_type: 'new_topic' | 'updated' | string;
  before: string | null;
  after: string;
  evidence_quotes: string[];
  why_it_matters: string;
  confidence: string;
}

export interface PolicyTopicLedger {
  topic_id: string;
  topic_name: string;
  first_seen_at: string | null;
  latest_seen_at: string | null;
  current_status: string;
  current_summary: string | null;
  latest_change?: PolicyChangeRecord | null;
  severity: string;
  msd_implication_latest: {
    rationale: string;
    next_action: string;
  };
  events: string[];
  impact_assessment_ready: boolean;
  data_gaps: string[];
}

export interface PolicyImpactCandidate {
  topic: string;
  title: string;
  priority: number;
  severity: string;
  rationale: string;
  next_action: string;
  event_count: number;
}

export interface PolicyReportArtifact {
  id: string;
  topic: string;
  kind: string;
  title: string;
  filename: string;
  format: string;
  available: boolean;
  file_size: number;
  updated_at: string | null;
  download_url: string | null;
}

export interface PolicyEvent {
  id: string;
  date: string | null;
  subject: string;
  summary: string;
  topic: string;
  agencies: string[];
  deadline: string | null;
  status: string;
  severity: string;
  email_body_chars: number;
  attachment_count: number;
  document_count: number;
}

export interface PolicyDocument {
  id: string;
  event_id: string;
  subject: string;
  topic: string;
  filename: string;
  status: string;
  char_count: number;
  source_kind: string;
  text_available: boolean;
}

export interface PolicyOverviewResponse {
  overview: PolicyOverview;
  topics: PolicyTopic[];
  topic_ledgers: PolicyTopicLedger[];
  impact_candidates: PolicyImpactCandidate[];
  report_artifacts: PolicyReportArtifact[];
  change_records?: PolicyChangeRecord[];
}

export async function fetchPolicyOverview(): Promise<PolicyOverviewResponse> {
  return api.get<PolicyOverviewResponse>('/api/policy-intelligence/overview');
}

export async function fetchPolicyEvents(): Promise<{ items: PolicyEvent[] }> {
  return api.get<{ items: PolicyEvent[] }>('/api/policy-intelligence/events');
}

export async function fetchPolicyDocuments(): Promise<{ items: PolicyDocument[] }> {
  return api.get<{ items: PolicyDocument[] }>('/api/policy-intelligence/documents');
}

export async function downloadPolicyReportArtifact(artifact: PolicyReportArtifact): Promise<void> {
  if (!artifact.download_url) throw new Error('다운로드 URL이 없습니다.');
  const token = getToken();
  const res = await fetch(artifact.download_url, {
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  });
  if (!res.ok) {
    let message = `다운로드 실패: HTTP ${res.status}`;
    try {
      const data = await res.json();
      if (data?.error) message = `다운로드 실패: ${data.error}`;
    } catch {
      // keep HTTP fallback
    }
    throw new Error(message);
  }
  const blob = await res.blob();
  const contentDisposition = res.headers.get('Content-Disposition') || '';
  const match = contentDisposition.match(/filename\*?=(?:UTF-8'')?([^;]+)/i);
  const filename = match ? decodeURIComponent(match[1].replace(/^"|"$/g, '')) : artifact.filename;
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
