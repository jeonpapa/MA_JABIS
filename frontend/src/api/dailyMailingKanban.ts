import { api } from './client';

// GET /api/admin/daily-mailing/kanban 계약 — agents/daily_mailing/storage.py:load_admin_kanban()
// 기사별 승인 없음(article_approval_required=false), 운영 상태 board.

export interface KanbanArticle {
  article_id: string;
  run_id: string;
  title: string;
  publisher_url: string | null;
  naver_url: string | null;
  source_name: string | null;
  source_tier: string | null;
  source_status: string | null;
  priority: string | null;
  ma_relevance: number | null;
  review_status: string | null;
  selected_for_draft: 0 | 1;
  score: number;
  published_at: string | null;
  keyword: string | null;
  verification_caveat: string | null;
  expires_at: string;
  created_at: string;
  quality_flags: string[];
  matched_keywords: string[];
  generated_at: string | null;
  html_path: string | null;
}

export interface KanbanLane {
  name: string;
  items: KanbanArticle[];
}

export interface KanbanRun {
  run_id: string;
  generated_at: string;
  window_label: string | null;
  lookback_hours: number;
  keywords_json: string;
  media_json: string;
  subscription_id: number | null;
  owner_email: string | null;
  recipients_json: string;
  delivery_status: string;
  approval_status: string;
  gmail_draft_id: string | null;
  gmail_message_id: string | null;
  sent_at: string | null;
  discovered_count: number;
  recent_count: number;
  selected_count: number;
  status: string;
  markdown_path: string | null;
  html_path: string | null;
  json_path: string | null;
  review_board_path: string | null;
  expires_at: string;
  created_at: string;
}

export interface DailyMailingKanban {
  status: string;
  retention_days: number;
  article_approval_required: boolean;
  lanes: KanbanLane[];
  runs: KanbanRun[];
}

export async function fetchDailyMailingKanban(): Promise<DailyMailingKanban> {
  return api.get<DailyMailingKanban>('/api/admin/daily-mailing/kanban');
}

// runs 테이블의 *_json 컬럼은 raw JSON 문자열 그대로 온다 — 파싱 실패 시 빈 배열로 폴백
export function parseJsonArray(raw: string | null | undefined): string[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.map(String) : [];
  } catch {
    return [];
  }
}
