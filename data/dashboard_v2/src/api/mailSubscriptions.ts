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
}

export interface MailSubscriptionInput {
  name: string;
  keywords: string[];
  media: string[];
  schedule: 'Daily' | 'Weekly';
  time: string;
  weekDay?: string | null;
  emails: string[];
  active?: boolean;
}

export interface MailSubListResponse {
  items: MailSubscription[];
  smtp_configured: boolean;
}

export interface TestSendResult {
  ok: boolean;
  mode: 'smtp' | 'dry-run' | 'none';
  recipients: string[];
  message?: string;
}

export async function listMailSubscriptions(): Promise<MailSubListResponse> {
  return api.get<MailSubListResponse>('/api/mail-subscriptions');
}

export async function createMailSubscription(input: MailSubscriptionInput): Promise<MailSubscription> {
  const r = await api.post<{ item: MailSubscription }>('/api/mail-subscriptions', input);
  return r.item;
}

export async function updateMailSubscription(
  id: number,
  patch: Partial<MailSubscriptionInput>,
): Promise<MailSubscription> {
  const r = await api.patch<{ item: MailSubscription }>(`/api/mail-subscriptions/${id}`, patch);
  return r.item;
}

export async function deleteMailSubscription(id: number): Promise<void> {
  await api.delete<{ ok: true }>(`/api/mail-subscriptions/${id}`);
}

export async function testSendMailSubscription(id: number): Promise<TestSendResult> {
  return api.post<TestSendResult>(`/api/mail-subscriptions/${id}/test-send`, {});
}

export interface MailPreview {
  subject: string;
  html: string;
  text: string;
}

export async function previewMailSubscription(id: number): Promise<MailPreview> {
  return api.post<MailPreview>(`/api/mail-subscriptions/${id}/preview`, {});
}

export async function previewAdHoc(name: string, keywords: string[], media: string[]): Promise<MailPreview> {
  const q = new URLSearchParams({
    name: name || 'Daily Dossier',
    keywords: keywords.join(','),
    media: media.join(','),
    format: 'json',
  });
  return api.get<MailPreview>(`/api/mailing/preview?${q.toString()}`);
}
