import { gradeClassOf, type SVGradeClass } from '@/api/stakeholder';

// 스케치 화면 공용 순수 헬퍼 — 라벨·날짜 표기를 스케치(001~004)의 문구 그대로 맞춘다.

/** 스케치 001/002 날짜 표기 2026.09.30 */
export function fmtDot(s?: string | null): string {
  if (!s) return '—';
  return s.slice(0, 10).replace(/-/g, '.');
}

/** 스케치 001 .mini 날짜 "9월 30일" */
export function monthDayKo(s?: string | null): string {
  if (!s) return '—';
  const [, m, d] = s.slice(0, 10).split('-');
  if (!m || !d) return s;
  return `${Number(m)}월 ${Number(d)}일`;
}

/** 스케치 003 fmtDate — Intl ko-KR (Asia/Seoul) 연·월·일 */
export function fmtKoDate(v?: string | null): string {
  if (!v) return '—';
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return v;
  return new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit' }).format(d);
}

/** 스케치 004 fmt — Intl ko-KR (Asia/Seoul) 연·월·일·시·분 */
export function fmtKoDateTime(v?: string | null): string {
  if (!v) return '—';
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return v;
  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(d);
}

/** 001 주제 탭 라벨(스케치 .tab 문구). 스케치에 없는 5번째 주제는 002 필터 문구를 쓴다. */
export const TOPIC_TAB_LABELS: Record<string, string> = {
  price_reform: '약가제도',
  access: '신약 접근성',
  rwe: 'RWE·성과평가',
  finance: '재정·지출관리',
  negotiation: '협상·공급·사후관리',
};

/** 002 좌측 주제 필터 라벨(스케치 .filters 문구 그대로) */
export const TOPIC_FILTER_LABELS: Record<string, string> = {
  price_reform: '약가제도 개편',
  access: '신약 접근성',
  rwe: 'RWE·성과평가',
  finance: '재정·지출관리',
  negotiation: '협상·공급·사후관리',
};

/** 001 표 .org 열 — 주제 짧은 이름 (교차 주제는 · 로 잇는다: 스케치 "신약 접근성·재정") */
export const TOPIC_ROW_LABELS: Record<string, string> = {
  price_reform: '약가제도 개편',
  access: '신약 접근성',
  rwe: 'RWE·성과평가',
  finance: '재정',
  negotiation: '협상·사후관리',
};

/** 증거등급 → 스케치 001 배지 문구 ("C · 직접인용", "B · 기관인용" …) */
export function gradeBadgeText(grade?: string | null): string {
  const tok = (grade || '').split(/[/+]/)[0]?.trim() ?? '';
  switch (tok) {
    case 'A':
      return 'A · 공식 원문';
    case 'B-quote':
      return 'B · 기관인용';
    case 'B-summary':
      return 'B · 기관요약';
    case 'C-quote':
      return 'C · 직접인용';
    case 'C-summary':
      return 'C · 기사요약';
    case 'Role-only':
      return '직책 확인';
    case 'Candidate-only':
      return '후보 확인';
    default:
      return tok || '등급 미표기';
  }
}

/** 배지 색 클래스 — 스케치 .a/.b/.c (role 은 회색 추가분) */
export function gradeCls(grade?: string | null, serverClass?: string | null): SVGradeClass {
  return gradeClassOf(grade, serverClass);
}

export const ORG_SHORT: Record<string, string> = {
  보건복지부: '복지부',
  건강보험심사평가원: '심평원',
  국민건강보험공단: '공단',
};

/** 받침 유무로 주격 조사(이/가) */
export function josaIGa(word: string): string {
  const ch = word.trim().slice(-1);
  const code = ch.charCodeAt(0) - 0xac00;
  if (code < 0 || code > 11171) return '이';
  return code % 28 === 0 ? '가' : '이';
}

export const PREPARING = '준비 중';
