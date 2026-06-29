import { api } from './client';

export interface PolicyOverview {
  created_at: string | null;
  source_batch_id: string;
  event_count: number;
  topic_count: number;
  document_count: number;
  high_impact_count: number;
  latest_event_date: string | null;
  severity_counts: Record<string, number>;
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

export interface PolicyImpactCandidate {
  topic: string;
  title: string;
  priority: number;
  severity: string;
  rationale: string;
  next_action: string;
  event_count: number;
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
  impact_candidates: PolicyImpactCandidate[];
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
