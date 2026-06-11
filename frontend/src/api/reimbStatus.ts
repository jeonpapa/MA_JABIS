import { api, getToken } from './client';

// ─────────────────────────────────────────────────────────────────────────────
// Reimbursement Status 페이지 전용 fetcher + adapter 모음.
// 서버(snake_case) 응답을 readdy 컴포넌트가 소비하는 뷰 모델로 변환한다.
// 원칙: 서버가 주지 않는 값은 만들지 않는다 (null → 컴포넌트에서 '정보 없음'/'—' 처리).
// 날짜 파생 필드(isPast/daysUntil/요일 등)는 클라이언트에서 결정적으로 계산한다.
// ─────────────────────────────────────────────────────────────────────────────

const DOW_KO = ['일', '월', '화', '수', '목', '금', '토'];

/** 'YYYY-MM-DD...' → 로컬 자정 Date (date-only) */
function parseDateOnly(s: string): Date {
  const [y, m, d] = s.slice(0, 10).split('-').map(Number);
  return new Date(y, m - 1, d);
}

/** 'YYYY-MM-DD[ HH:MM:SS]' → 'YYYY.MM.DD' (null 은 그대로) */
function fmtDot(s: string | null | undefined): string | null {
  if (!s) return null;
  return s.slice(0, 10).replace(/-/g, '.');
}

function todayDateOnly(): Date {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate());
}

/** 심의 결과 state/stateLabel → 색상 버킷 */
export type ResultKind = 'approved' | 'rejected' | 'pending';

export function stateKind(state: string | null, label?: string | null): ResultKind {
  const s = (state ?? '').toUpperCase();
  const l = label ?? '';
  if (
    s.includes('REJECT') || s.includes('FAIL') || s.includes('NOT_LISTED') ||
    l.includes('미설정') || l.includes('거절') || l.includes('미통과') || l.includes('비급여')
  ) return 'rejected';
  if (
    s.includes('PASS') || s.includes('APPROV') || s.includes('LISTED') || s.includes('COMPLETE') ||
    l.includes('통과') || l.includes('설정') || l.includes('적정') || l.includes('허가') || l.includes('완료') || l.includes('등재')
  ) return 'approved';
  return 'pending';
}

// ═════════════════════════════════════════════════════════════════════════════
// 1. 연간 일정  (GET /api/reimbursement/meetings)
// ═════════════════════════════════════════════════════════════════════════════

interface RawMeeting {
  id: number;
  committee: 'cancer' | 'evaluation';
  year: number;
  cycle: number;
  date: string; // 'YYYY-MM-DD'
  status: 'SCHEDULED' | 'COMPLETED';
  note: string | null;
  minutes_url: string | null;
}

export interface MeetingSchedule {
  id: number;
  year: number;
  month: number;
  monthLabel: string;
  type: 'cancer' | 'evaluation';
  typeLabel: string;
  cycle: string;       // '6차'
  date: string;        // 'YYYY.MM.DD'
  dayOfWeek: string;   // '수'
  isPast: boolean;
  isToday: boolean;
  isUpcoming: boolean;
  daysUntil: number;
  status: 'SCHEDULED' | 'COMPLETED';
  note?: string;
}

function adaptMeeting(m: RawMeeting, today: Date): MeetingSchedule {
  const d = parseDateOnly(m.date);
  const daysUntil = Math.round((d.getTime() - today.getTime()) / 86_400_000);
  return {
    id: m.id,
    year: m.year,
    month: d.getMonth() + 1,
    monthLabel: `${d.getMonth() + 1}월`,
    type: m.committee,
    typeLabel: m.committee === 'cancer' ? '암질심' : '약평위',
    cycle: `${m.cycle}차`,
    date: fmtDot(m.date) as string,
    dayOfWeek: DOW_KO[d.getDay()],
    isPast: daysUntil < 0,
    isToday: daysUntil === 0,
    isUpcoming: daysUntil > 0,
    daysUntil,
    status: m.status,
    note: m.note ?? undefined,
  };
}

export async function fetchMeetings(): Promise<MeetingSchedule[]> {
  const res = await api.get<{ items: RawMeeting[] }>('/api/reimbursement/meetings');
  const today = todayDateOnly();
  return (res.items ?? []).map(m => adaptMeeting(m, today));
}

// ═════════════════════════════════════════════════════════════════════════════
// 2. 파이프라인 보드  (GET /api/reimbursement/pipeline)
// ═════════════════════════════════════════════════════════════════════════════

export interface DrugHistoryItem {
  id: number;
  date: string | null;        // 'YYYY.MM.DD'
  committee: string;          // '암질심' | '약평위'
  state: string;
  stateLabel: string;
  sessionId: number | null;
  sessionDate: string | null; // 'YYYY.MM.DD'
  cycle: number | null;
  attempt: number | null;
  evidenceUrl: string | null;
}

export interface TimelineStep {
  phase: string;
  date: string | null;        // 'YYYY.MM.DD'
  status: 'done' | 'upcoming' | 'in_progress' | 'rejected';
  negotiationStatus?: string | null;
}

export interface PipelineDrug {
  id: number;
  name: string;
  nameEn: string | null;
  ingredient: string | null;
  company: string | null;
  indication: string | null;
  type: string | null;
  msdFlag: boolean;
  trackingPriority: string | null;
  status: 'completed' | 'scheduled' | 'waiting';
  submittedDate: string | null;
  amjilsimPassDate: string | null;
  yakpyungwiPassDate: string | null;
  negotiationStatus: string | null;
  notes: string | null;
  updatedDate: string | null; // 'YYYY.MM.DD'
  history: DrugHistoryItem[];
  timeline: TimelineStep[];
}

export interface PipelineStage {
  id: 'cancer' | 'evaluation' | 'nhis';
  count: number;
  drugs: PipelineDrug[];
}

interface RawPipelineDrug extends Omit<PipelineDrug, 'history' | 'timeline'> {
  history: Array<Omit<DrugHistoryItem, never>>;
  timeline: TimelineStep[];
}

function adaptDrug(d: RawPipelineDrug): PipelineDrug {
  return {
    ...d,
    submittedDate: fmtDot(d.submittedDate),
    amjilsimPassDate: fmtDot(d.amjilsimPassDate),
    yakpyungwiPassDate: fmtDot(d.yakpyungwiPassDate),
    updatedDate: fmtDot(d.updatedDate),
    history: (d.history ?? []).map(h => ({
      ...h,
      date: fmtDot(h.date),
      sessionDate: fmtDot(h.sessionDate),
    })),
    timeline: (d.timeline ?? []).map(t => ({ ...t, date: fmtDot(t.date) })),
  };
}

export async function fetchPipeline(): Promise<PipelineStage[]> {
  const res = await api.get<{ stages: Array<{ id: PipelineStage['id']; count: number; drugs: RawPipelineDrug[] }> }>(
    '/api/reimbursement/pipeline'
  );
  return (res.stages ?? []).map(s => ({
    id: s.id,
    count: s.count,
    drugs: (s.drugs ?? []).map(adaptDrug),
  }));
}

// ═════════════════════════════════════════════════════════════════════════════
// 3. 회차 심의 결과  (GET /api/reimbursement/meetings/<id>/results)
// ═════════════════════════════════════════════════════════════════════════════

export interface MeetingResultDrug {
  drugId: number;
  name: string;
  nameEn: string | null;
  ingredient: string | null;
  company: string | null;
  indication: string | null;
  state: string;
  stateLabel: string;
  attempt: number | null;
  evidenceUrl: string | null;
}

export interface MeetingResultData {
  meeting: RawMeeting;
  totals: { reviewed: number; approved: number; rejected: number; withdrawn: number; pending: number };
  drugs: MeetingResultDrug[];
  report: { id: number; title: string; summary: string | null; highlights: string[] } | null;
}

export function fetchMeetingResults(meetingId: number): Promise<MeetingResultData> {
  return api.get<MeetingResultData>(`/api/reimbursement/meetings/${meetingId}/results`);
}

// ═════════════════════════════════════════════════════════════════════════════
// 4. Intelligence Reports  (GET /api/reimbursement/reports)
// ═════════════════════════════════════════════════════════════════════════════

interface RawReport {
  id: number;
  file_name: string;
  pdf_path: string;
  file_size: number | null;
  pages: number | null;
  title: string | null;
  committee: 'cancer' | 'evaluation' | null;
  report_type: 'pre' | 'post' | 'monthly' | 'other' | null;
  year: number | null;
  cycle: number | null;
  session_date: string | null;
  summary: string | null;
  highlights: string[] | null;
  analyzed: number;
  source: string | null;
  created_at: string | null;
  analyzed_at: string | null;
}

export type ReportCategory =
  | 'pre-cancer' | 'post-cancer' | 'pre-evaluation' | 'post-evaluation' | 'monthly' | 'other';

export const REPORT_CATEGORY_LABELS: Record<ReportCategory, string> = {
  'pre-cancer': '암질심 사전 분석',
  'post-cancer': '암질심 결과 리뷰',
  'pre-evaluation': '약평위 사전 분석',
  'post-evaluation': '약평위 결과 리뷰',
  monthly: '월간 트렌드',
  other: '기타',
};

export interface IntelligenceReport {
  id: number;
  title: string;
  category: ReportCategory;
  categoryLabel: string;
  year: number;
  date: string;       // 'YYYY.MM.DD' (생성일)
  cycle: string;      // '2026년 6차 (2026-06-04)'
  summary: string;
  highlights: string[];
  fileSize: string;   // '425 KB'
  pages: number | null;
  downloadId: number; // /api/reimbursement/reports/<id>/pdf
}

function formatBytes(bytes: number | null): string {
  if (!bytes || bytes <= 0) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function reportCategory(r: RawReport): ReportCategory {
  if (r.report_type === 'pre' || r.report_type === 'post') {
    return `${r.report_type}-${r.committee === 'cancer' ? 'cancer' : 'evaluation'}` as ReportCategory;
  }
  if (r.report_type === 'monthly') return 'monthly';
  return 'other';
}

function adaptReport(r: RawReport): IntelligenceReport {
  const category = reportCategory(r);
  const cycleParts: string[] = [];
  if (r.year != null) cycleParts.push(`${r.year}년`);
  if (r.cycle != null) cycleParts.push(`${r.cycle}차`);
  const cycle = cycleParts.length > 0
    ? `${cycleParts.join(' ')}${r.session_date ? ` (${r.session_date})` : ''}`
    : (r.session_date ?? '회차 정보 없음');
  return {
    id: r.id,
    title: r.title ?? r.file_name,
    category,
    categoryLabel: REPORT_CATEGORY_LABELS[category],
    year: r.year ?? new Date().getFullYear(),
    date: fmtDot(r.created_at) ?? '—',
    cycle,
    summary: r.summary ?? '',
    highlights: r.highlights ?? [],
    fileSize: formatBytes(r.file_size),
    pages: r.pages,
    downloadId: r.id,
  };
}

export async function fetchReports(): Promise<IntelligenceReport[]> {
  const res = await api.get<{ items: RawReport[]; inbox_dir?: string }>('/api/reimbursement/reports');
  return (res.items ?? []).map(adaptReport);
}

/**
 * PDF 다운로드/열기 — Bearer 토큰이 필요하므로 plain <a href> 가 아니라
 * fetch blob → objectURL 로 새 탭에서 연다.
 */
export async function openReportPdf(reportId: number): Promise<void> {
  const token = getToken();
  const headers: Record<string, string> = {};
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(`/api/reimbursement/reports/${reportId}/pdf`, { headers });
  if (!res.ok) throw new Error(`PDF 다운로드 실패 (HTTP ${res.status})`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  window.open(url, '_blank', 'noopener');
  // 새 탭 로드 후 정리
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
