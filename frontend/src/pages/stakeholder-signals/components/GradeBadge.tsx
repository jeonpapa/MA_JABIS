import { gradeLabel } from '@/api/stakeholder';
import { gradeBadgeText, gradeCls } from '../sketch/helpers';
import { EVIDENCE_LOCATOR_META, INTEGRITY_META, MATCH_META, integrityTitle } from './evidence';

// 증거 배지 — 스케치 001 의 .badge(.a/.b/.c) 와, 스케치에 자리가 없는 원문 대조·무결성 표시(.svb, common.css).
// 규칙(stakeholder_verbatim_rules.md §4.2): "원문 대조 완료"(=SHA-256 일치, 해시는 툴팁) 는 integrity_status==='verified' 일 때만,
// locator_match==='none' 은 숨기지 않고 빨간 "원문 불일치" 로 드러낸다.

/** 스케치 001 표 .badge — "C · 직접인용" */
export function GradeBadge({ grade, gradeClass, className = 'badge' }: { grade?: string | null; gradeClass?: string | null; className?: string }) {
  const cls = gradeCls(grade, gradeClass);
  return (
    <span className={`${className} ${cls === 'role' ? 'role' : cls}`} title={`증거등급 ${grade || '미표기'} — ${gradeLabel(grade)}`}>
      {gradeBadgeText(grade)}
    </span>
  );
}

/** 등급 배지(.svb) — 인용 단위 표시용. 화면은 한국어 라벨('언론 직접인용'), 코드(C-quote)는 툴팁. */
export function GradeMark({ grade, gradeClass }: { grade?: string | null; gradeClass?: string | null }) {
  const cls = gradeCls(grade, gradeClass);
  const tone = cls === 'a' ? 'ok' : cls === 'b' ? 'info' : cls === 'c' ? 'warn' : 'neutral';
  return (
    <span className={`svb ${tone}`} title={`증거등급 코드: ${grade || '미표기'}`}>
      {gradeLabel(grade)}
    </span>
  );
}

export function LocatorMatchBadge({ match, claim = false }: { match?: string | null; /** 정책 시그널 근거(요약 claim) */ claim?: boolean }) {
  const meta = claim ? EVIDENCE_LOCATOR_META : MATCH_META;
  const m = meta[(match || 'unchecked').toLowerCase()] ?? meta.unchecked;
  return (
    <span className={`svb ${m.tone}`} title={m.title}>
      {m.label}
    </span>
  );
}

export function IntegrityBadge({ status, hash }: { status?: string | null; hash?: string | null }) {
  const m = INTEGRITY_META[(status || 'unchecked').toLowerCase()] ?? INTEGRITY_META.unchecked;
  return (
    <span className={`svb ${m.tone}`} title={integrityTitle(status, hash)}>
      {m.label}
    </span>
  );
}
