import { api } from './client';

export interface GovernmentSummarySource {
  title: string;
  url: string;
  source: string;
  date: string;
}

export interface GovernmentSummaryResponse {
  updated_at: string;
  markdown: string;
  reviewers: string[];
  sources: GovernmentSummarySource[];
  keywords?: string[];
  error?: string;
}

export async function fetchGovernmentSummary(refresh = false): Promise<GovernmentSummaryResponse> {
  const q = refresh ? '?refresh=1' : '';
  return api.get<GovernmentSummaryResponse>(`/api/home/government-keyword-summary${q}`);
}
