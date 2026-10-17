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
  id: number | null;
  date: string | null;        // 'YYYY.MM.DD'
  committee: string;          // '암질심' | '약평위'
  state: string;
  stateLabel: string;
  sessionId: number | null;
  sessionDate: string | null; // 'YYYY.MM.DD'
  cycle: number | null;
  year?: number | null;       // 차수의 연도 (회의 연결 시)
  cycleLabel?: string | null; // '2025년 9차' — 연도 없는 차수는 해마다 반복되어 모호
  attempt: number | null;
  evidenceUrl: string | null; // http(s) 링크일 때만
  evidenceText?: string | null; // 링크가 아닌 근거 메모 (내부 키 제거)
  synthetic?: boolean;        // 통과일 컬럼 기반 합성 이벤트 (큐 미수집 차수)
}

/** 마지막 결과 — 위원회 이벤트 또는 공단 협상 결과 중 사건일이 가장 늦은 것 */
export interface LastResult {
  kind: 'committee' | 'negotiation';
  committee: 'cancer' | 'evaluation' | null;
  committeeLabel: string;     // '암질심' | '약평위' | '건보공단'
  state: string;
  label: string;              // '통과' | '재심의' | '대기' | '협상 타결' ...
  date: string | null;        // 'YYYY-MM-DD' (협상은 'YYYY-MM-01')
  month?: string | null;      // 협상 완료/등록 연월 'YYYY-MM'
  sessionLinked: boolean;
  cycleLabel: string | null;
  source: string;             // 'queue' | 'pass_date' | 'nhis_official' | 'manual'
}

/** 카드가 이 일수보다 오래 갱신되지 않으면 경고 (백엔드 STALE_DAYS 와 동기) */
export const STALE_DAYS = 60;

/** 예상일의 성격 — forecast(미래 차수) / overdue(예상 차수가 지났는데 결과 미반영: 예보가 아니라 경과) */
export type ExpectedStatus = 'forecast' | 'overdue';

export interface TimelineStep {
  phase: string;
  date: string | null;        // 'YYYY.MM.DD' (실측 통과일)
  expectedDate?: string | null; // 'YYYY.MM.DD' (위원회 일정 기반 예상일 — 실측과 분리)
  expectedStatus?: ExpectedStatus | null;
  // not_applicable: 비항암 약제의 암질심 단계 (약평위 직행) — 예상일을 달지 않는다
  status: 'done' | 'upcoming' | 'in_progress' | 'rejected' | 'expected' | 'not_applicable';
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
  status: 'completed' | 'negotiating' | 'scheduled' | 'waiting';
  expectedSessionId?: number | null;
  expectedSessionDate?: string | null; // 'YYYY.MM.DD' — 상정예정 차수 일정
  expectedSessionCycle?: number | null; // 상정예정 차수 번호 (배지 "7차")
  expectedSessionYearLabel?: string | null; // 상정예정 차수 연도 '2026년 ' (연도 없는 차수 모호 방지)
  expectedStatus?: ExpectedStatus | null; // 타임라인 미도래 단계 예상일의 성격 (overdue 하나라도 있으면 overdue)
  submittedDate: string | null;
  amjilsimPassDate: string | null;
  yakpyungwiPassDate: string | null;
  negotiationStatus: string | null;
  // NHIS 공식 협상자료 (건강보험공단 공개자료 매칭 시)
  negotiationCompleteDate: string | null; // 'YYYY.MM' 협상완료연월
  negotiationDateSource: string | null;   // 'nhis_official' | 'manual' | null
  nhisRegisteredYm: string | null;         // 'YYYY.MM' 공단 등록연월
  efficacyGroup: string | null;            // 공단 효능군
  listType: '신규' | '확대' | null;        // NHIS 신규/사용범위 확대 구분 (보드 필터칩)
  notes: string | null;
  keyIssues: string[];        // 핵심 쟁점 (D±1 보고서 전사, 내부 키 제거·연도 보강)
  keyIssuesRaw?: string[];    // 원문
  updatedDate: string | null; // 'YYYY.MM.DD' — 상태 기록(큐 관측·공단 자료 변화)이 마지막으로 바뀐 날
  nhisCheckedAt?: string | null; // 'YYYY.MM.DD' — 공단 목록에서 마지막으로 확인한 날
  staleDays?: number | null;  // 최근 갱신 이후 경과일
  isStale?: boolean;          // STALE_DAYS 초과 (공단 자료로 최근 확인된 건 제외)
  nextStep?: string;          // '암질심 심의' | '약평위 심의' | '건보공단 협상' | '협상 완료'
  lastResult?: LastResult | null;
  history: DrugHistoryItem[];
  timeline: TimelineStep[];
  // 등재 차수(listing round) — 같은 브랜드도 차수별 카드. roundId null = 분할 전(약제 행 = 차수)
  drugId?: number;
  cardKey?: string;                 // `${drugId}:${roundId ?? 0}` — 카드 React key
  roundId?: number | null;
  roundLabel?: string | null;       // '2026 확대 · 파드셉 병용 요로상피암 1차'
  roundSeq?: number;
  roundCount?: number;
  roundSource?: string | null;
  roundNeedsReview?: boolean;
  comboKey?: string | null;
  comboPartners?: { drugId: number; name: string | null; roundId: number; roundLabel: string }[];
  reimbStartDate?: string | null;   // 'YYYY.MM.DD' 급여 시행일
  indicationScope?: string | null;
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

/** 'YYYY-MM' → 'YYYY.MM' (협상연월; null 그대로) */
function fmtYm(s: string | null | undefined): string | null {
  if (!s) return null;
  return s.slice(0, 7).replace(/-/g, '.');
}

function adaptDrug(d: RawPipelineDrug): PipelineDrug {
  return {
    ...d,
    keyIssues: d.keyIssues ?? [],
    expectedSessionDate: fmtDot(d.expectedSessionDate),
    expectedSessionYearLabel: d.expectedSessionDate ? `${d.expectedSessionDate.slice(0, 4)}년 ` : null,
    submittedDate: fmtDot(d.submittedDate),
    amjilsimPassDate: fmtDot(d.amjilsimPassDate),
    yakpyungwiPassDate: fmtDot(d.yakpyungwiPassDate),
    negotiationCompleteDate: fmtYm(d.negotiationCompleteDate),
    nhisRegisteredYm: fmtYm(d.nhisRegisteredYm),
    updatedDate: fmtDot(d.updatedDate),
    nhisCheckedAt: fmtDot(d.nhisCheckedAt),
    reimbStartDate: fmtDot(d.reimbStartDate),
    history: (d.history ?? []).map(h => ({
      ...h,
      date: fmtDot(h.date),
      sessionDate: fmtDot(h.sessionDate),
    })),
    timeline: (d.timeline ?? []).map(t => ({
      ...t, date: fmtDot(t.date), expectedDate: fmtDot(t.expectedDate),
    })),
  };
}

export interface PipelineBoardData {
  stages: PipelineStage[];
  /** 공단 협상 완료(AGREED)로 보드에서 빠진 약제 수 — '협상 완료 내역' 에서 확인 */
  completedExcluded: number;
}

export async function fetchPipelineBoard(): Promise<PipelineBoardData> {
  const res = await api.get<{
    stages: Array<{ id: PipelineStage['id']; count: number; drugs: RawPipelineDrug[] }>;
    completedExcluded?: number;
  }>('/api/reimbursement/pipeline');
  return {
    stages: (res.stages ?? []).map(s => ({
      id: s.id,
      count: s.count,
      drugs: (s.drugs ?? []).map(adaptDrug),
    })),
    completedExcluded: res.completedExcluded ?? 0,
  };
}

export async function fetchPipeline(): Promise<PipelineStage[]> {
  return (await fetchPipelineBoard()).stages;
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

/** 회차 결과의 상태 — not_ingested(지났는데 미반영: drugs=[] 는 '없음' 이 아니라 '모름') / empty(닫혔고 0건) */
export type MeetingResultsStatus = 'upcoming' | 'not_ingested' | 'available' | 'empty';

export interface MeetingResultData {
  meeting: RawMeeting;
  totals: { reviewed: number; approved: number; rejected: number; withdrawn: number; pending: number };
  drugs: MeetingResultDrug[];
  report: { id: number; title: string; summary: string | null; highlights: string[] } | null;
  resultsStatus?: MeetingResultsStatus;
  resultsNote?: string | null;
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
  // 표시용 파생 (agents/reimb_reports.report_dates) — session_date 는 출처별로 작성일/회의일이 섞여 있다
  report_date?: string | null;        // 파일명 날짜 = 보고서 작성일
  meeting_date?: string | null;       // 위원회·연도·차수로 찾은 회의일
  report_offset_days?: number | null; // 작성일 - 회의일 (-1 = 회의 하루 전)
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
  date: string;       // 'YYYY.MM.DD' (정렬 기준: 보고서 작성일 → 없으면 등록일)
  reportDate: string | null;  // 'YYYY.MM.DD' 보고서 작성일 (파일명)
  meetingDate: string | null; // 'YYYY.MM.DD' 회의일 (연간 일정과 같은 날짜)
  registeredDate: string | null; // 'YYYY.MM.DD' 시스템 등록일
  offsetLabel: string | null; // 'D-1' | 'D+1'
  cycle: string;      // '2026년 8차 암질심'
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
  const committeeKr = r.committee === 'cancer' ? '암질심' : r.committee === 'evaluation' ? '약평위' : '';
  if (cycleParts.length > 0 && committeeKr) cycleParts.push(committeeKr);
  // 회차 라벨에는 회의일만 쓴다 — 예전엔 session_date(=보고서 작성일 D-1/D+1)를 괄호에 넣어
  // '8차 (2026-09-29)' 가 연간 일정 '8차 09.30' 과 충돌해 보였다.
  const cycle = cycleParts.length > 0 ? cycleParts.join(' ') : '회차 정보 없음';
  const off = r.report_offset_days;
  const offsetLabel = off == null ? null : off === 0 ? '당일' : off < 0 ? `D${off}` : `D+${off}`;
  const reportDate = fmtDot(r.report_date);
  const registeredDate = fmtDot(r.created_at);
  return {
    id: r.id,
    title: r.title ?? r.file_name,
    category,
    categoryLabel: REPORT_CATEGORY_LABELS[category],
    year: r.year ?? new Date().getFullYear(),
    date: reportDate ?? registeredDate ?? '—',
    reportDate,
    meetingDate: fmtDot(r.meeting_date),
    registeredDate,
    offsetLabel,
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

// ═════════════════════════════════════════════════════════════════════════════
// 5. NHIS 약가협상 공개자료  (GET /api/reimbursement/nhis-negotiations)
//    건강보험공단 '신약'/'사용범위 확대' 공식 협상 결과 아카이브.
// ═════════════════════════════════════════════════════════════════════════════

export interface NhisNegotiation {
  id: number;
  listType: '신규' | '확대';
  productName: string;
  manufacturer: string | null;
  efficacyGroup: string | null;
  registeredYm: string | null;   // 'YYYY.MM' 등록연월
  result: string | null;          // 협상결과 (합의 등)
  completedYm: string | null;     // 'YYYY.MM' 협상완료연월
  inProgress: boolean;
  matched: boolean;
  drugId: number | null;
  sourceUrl: string;
  fetchedAt: string | null;
  drugName?: string | null;       // 매칭된 추적 약제명
  drugIndication?: string | null; // 매칭된 추적 약제 적응증 (병용 등재 맥락)
  roundId?: number | null;        // 연결된 등재 차수
  roundLabel?: string | null;     // '2026 확대 · 파드셉 병용 요로상피암 1차' — 같은 브랜드의 어느 차수 협상인지
}

export interface NhisNegotiationResult {
  items: NhisNegotiation[];
  // superseded: 완료 행으로 대체되어 목록·카운트에서 빠진 '협상 중' 아카이브 행 수
  counts: { completed: number; in_progress: number; total: number; superseded?: number };
}

interface RawNhisNegotiation extends Omit<NhisNegotiation, 'registeredYm' | 'completedYm'> {
  registeredYm: string | null;
  completedYm: string | null;
}

function adaptNhis(r: RawNhisNegotiation): NhisNegotiation {
  return { ...r, registeredYm: fmtYm(r.registeredYm), completedYm: fmtYm(r.completedYm) };
}

export async function fetchNhisNegotiations(params?: {
  status?: 'completed' | 'in_progress' | 'all';
  listType?: '신규' | '확대' | '';
  q?: string;
}): Promise<NhisNegotiationResult> {
  const qs = new URLSearchParams();
  if (params?.status) qs.set('status', params.status);
  if (params?.listType) qs.set('list_type', params.listType);
  if (params?.q) qs.set('q', params.q);
  const suffix = qs.toString();
  const res = await api.get<{ items: RawNhisNegotiation[]; counts: NhisNegotiationResult['counts'] }>(
    `/api/reimbursement/nhis-negotiations${suffix ? `?${suffix}` : ''}`
  );
  return {
    items: (res.items ?? []).map(adaptNhis),
    counts: res.counts ?? { completed: 0, in_progress: 0, total: 0 },
  };
}
