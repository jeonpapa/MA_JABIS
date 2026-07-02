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
  committee_event_count?: number;
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

export interface PolicyEventDocument {
  id: string;
  filename: string | null;
  char_count: number;
  status: string | null;
  text_available: boolean;
  file_available: boolean;
  text_url: string | null;
  download_url: string | null;
}

export interface PolicyEventDetail {
  id: string;
  subject: string | null;
  date: string | null;
  from: string | null;
  topic: string;
  agencies: string[];
  severity: string;
  status: string;
  deadline: string | null;
  email_body: string;
  email_body_chars: number;
  documents: PolicyEventDocument[];
}

export async function fetchPolicyOverview(): Promise<PolicyOverviewResponse> {
  return api.get<PolicyOverviewResponse>('/api/policy-intelligence/overview');
}

export async function fetchPolicyEventDetail(eventId: string): Promise<PolicyEventDetail> {
  return api.get<PolicyEventDetail>(`/api/policy-intelligence/events/${encodeURIComponent(eventId)}`);
}

// ── KRPIA 위원회 워크스페이스 (Monthly Meeting + TF) ─────────────────────────
export interface CommitteeDoc {
  id: string;
  filename: string | null;
  char_count: number;
  text_available: boolean;
  file_available: boolean;
  text_url: string | null;
  download_url: string | null;
}

export interface CommitteeDiscussedTopic {
  topic: string;
  severity: string;
  rationale: string;
  next_action: string;
}

export interface MonthlyMeeting {
  event_id: string;
  subject: string | null;
  received_utc: string | null;
  month: string;
  meeting_no: string | null;
  agencies: string[];
  documents: CommitteeDoc[];
  discussed_topics: CommitteeDiscussedTopic[];
}

export interface CommitteeMaterial {
  event_id: string;
  subject: string | null;
  received_utc: string | null;
  agencies: string[];
  documents: CommitteeDoc[];
}

export interface CommitteeTf {
  id: string;
  name: string;
  description: string;
  materials: CommitteeMaterial[];
  material_count: number;
  latest_date: string | null;
  document_count: number;
}

export interface CommitteeWorkspace {
  summary: { monthly_count: number; tf_count: number; tf_with_materials: number };
  monthly_meetings: MonthlyMeeting[];
  tfs: CommitteeTf[];
}

export interface PolicySearchResult {
  type: 'event' | 'document';
  event_id: string;
  doc_id?: string;
  filename?: string;
  subject: string | null;
  topic: string;
  lane: 'monthly' | 'tf' | 'policy' | string;
  tf_name: string | null;
  date: string | null;
  snippet: string;
}

export async function fetchCommitteeWorkspace(): Promise<CommitteeWorkspace> {
  return api.get<CommitteeWorkspace>('/api/policy-intelligence/committee');
}

export async function searchPolicy(q: string): Promise<{ query: string; count: number; results: PolicySearchResult[] }> {
  return api.get(`/api/policy-intelligence/search?q=${encodeURIComponent(q)}`);
}

export async function fetchPolicyDocumentText(docId: string): Promise<{ id: string; filename: string; text: string }> {
  return api.get(`/api/policy-intelligence/documents/${encodeURIComponent(docId)}/text`);
}

export async function downloadPolicyDocument(doc: PolicyEventDocument): Promise<void> {
  if (!doc.download_url) throw new Error('다운로드 URL이 없습니다.');
  const token = getToken();
  const res = await fetch(doc.download_url, {
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  });
  if (!res.ok) {
    let message = `다운로드 실패: HTTP ${res.status}`;
    try {
      const data = await res.json();
      if (data?.error) message = `다운로드 실패: ${data.error}`;
    } catch { /* keep */ }
    throw new Error(message);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = doc.filename || 'document';
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
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
