import { api } from './client';

export interface MailSubscription {
  id: number;
  name: string;
  keywords: string[];
  media: string[];
  schedule: 'Daily' | 'Weekly';
  time: string;
  weekDay: string | null;
  emails: string[];
  active: boolean;
  created_at: string;
  updated_at: string;
  last_sent_at: string | null;
  companies?: string[];
  brands?: string[];
  policy_topics?: string[];
  disease_areas?: string[];
  custom_sources?: { url: string; name?: string }[];
  deliveryMode?: 'approval' | 'auto';
  includeTopMaNews?: boolean;
}

export interface MailSubscriptionInput {
  name: string;
  keywords: string[];
  media?: string[];
  schedule: 'Daily' | 'Weekly';
  time: string;
  weekDay?: string | null;
  emails: string[];
  active?: boolean;
  companies?: string[];
  brands?: string[];
  policyTopics?: string[];
  diseaseAreas?: string[];
  customSources?: { url: string; name?: string }[];
  deliveryMode?: 'approval' | 'auto';
  includeTopMaNews?: boolean;
}

export interface MailSubSaveResult { item: MailSubscription; warnings: string[]; }

export interface MailSubListResponse {
  items: MailSubscription[];
  smtp_configured: boolean;
}

export interface TestSendResult {
  ok: boolean;
  mode: 'smtp' | 'dry-run' | 'none' | 'preview';
  sent?: boolean;
  recipients: string[];
  subject?: string;
  html?: string;
  text?: string;
  message?: string;
}

export interface SubscriptionScopeResponse {
  scope: Record<string, unknown>;
}

// POST /api/mail-subscriptions/:id/test-request — 202 {ok, message} / 409 {error, code: 'IN_PROGRESS'} (ApiError 로 throw)
export interface TestMailRequestResult {
  ok: boolean;
  message?: string;
}

export async function listMailSubscriptions(): Promise<MailSubListResponse> {
  return api.get<MailSubListResponse>('/api/mail-subscriptions');
}

export async function createMailSubscription(input: MailSubscriptionInput): Promise<MailSubSaveResult> {
  const r = await api.post<{ item: MailSubscription; warnings?: string[] }>('/api/mail-subscriptions', input);
  return { item: r.item, warnings: r.warnings ?? [] };
}

export async function updateMailSubscription(
  id: number,
  patch: Partial<MailSubscriptionInput>,
): Promise<MailSubSaveResult> {
  const r = await api.patch<{ item: MailSubscription; warnings?: string[] }>(`/api/mail-subscriptions/${id}`, patch);
  return { item: r.item, warnings: r.warnings ?? [] };
}

export async function deleteMailSubscription(id: number): Promise<void> {
  await api.delete<{ ok: true }>(`/api/mail-subscriptions/${id}`);
}

export async function testSendMailSubscription(id: number): Promise<TestSendResult> {
  return api.post<TestSendResult>(`/api/mail-subscriptions/${id}/test-send`, {});
}

export async function fetchSubscriptionScope(id: number): Promise<SubscriptionScopeResponse> {
  return api.get<SubscriptionScopeResponse>(`/api/mail-subscriptions/${id}/scope`);
}

export async function requestTestMail(id: number): Promise<TestMailRequestResult> {
  return api.post<TestMailRequestResult>(`/api/mail-subscriptions/${id}/test-request`, {});
}
