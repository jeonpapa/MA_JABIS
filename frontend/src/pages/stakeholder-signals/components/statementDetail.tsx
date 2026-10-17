import { useState } from 'react';
import {
  clampExcerpt,
  EXCERPT_MAX_CHARS,
  fetchExcerpt,
  type SVExcerpt,
  type SVProvenance,
  type SVQuote,
} from '@/api/stakeholder';
import { GradeMark, IntegrityBadge, LocatorMatchBadge } from './GradeBadge';
import { isMismatch } from './evidence';

// 발언 상세 공용 로직 — 001 드로어와 002 상세 패널이 같은 데이터·증거 규칙을 다른 스케치 마크업으로 그린다.
// 증거 규칙(stakeholder_verbatim_rules.md §2-1·§4.2·§5):
//   verbatim 은 저장 문자열 그대로(따옴표 제거·교정 없음), 귀속요약·분석과 라벨로 분리.
//   인용마다 등급·원문 대조·무결성 배지, locator_match==='none' 은 빨간 "원문 불일치".
//   원문은 locator 창 발췌(≤600자)만.

/** 인용 1건의 증거 배지 3종 + (불일치면) 경고. */
export function QuoteMarks({
  quote,
  prov,
  statementGrade,
  statementGradeClass,
}: {
  quote: SVQuote;
  prov?: SVProvenance | null;
  statementGrade?: string | null;
  statementGradeClass?: string | null;
}) {
  return (
    <div className="svb-row">
      <GradeMark grade={quote.evidence_grade || statementGrade} gradeClass={quote.evidence_grade ? null : statementGradeClass} />
      <LocatorMatchBadge match={quote.locator_match} />
      <IntegrityBadge status={prov?.integrity_status} hash={prov?.text_sha256 || prov?.sha256} />
    </div>
  );
}

export function MismatchWarning({ match }: { match?: string | null }) {
  if (!isMismatch(match)) return null;
  return (
    <p className="svx-mismatch" role="alert">
      원문 불일치 — 이 인용 문구를 근거 원문에서 찾지 못했습니다. 인용·보고 전 원문 확인이 필요합니다.
    </p>
  );
}

/** 내부 파일 경로(sources/…txt:94-96)는 기본으로 숨기고 '근거 위치' 펼침으로만 보여 준다. */
export function LocatorDisclosure({ locator }: { locator?: string | null }) {
  if (!locator) return null;
  return (
    <details className="svx-loc">
      <summary>근거 위치</summary>
      <code title="내부 원문 파일 경로 · 줄 범위">{locator}</code>
    </details>
  );
}

export function ExcerptBox({ excerpt }: { excerpt: SVExcerpt }) {
  const { text, clamped } = clampExcerpt(excerpt.text);
  return (
    <div className="svx-excerpt">
      <span className="svx-label">원문 발췌 (최대 {EXCERPT_MAX_CHARS}자)</span>
      <LocatorDisclosure locator={excerpt.locator} />
      {text}
      {clamped && <span style={{ color: '#888' }}> … (발췌 상한 {EXCERPT_MAX_CHARS}자)</span>}
    </div>
  );
}

/** 발췌 열기/닫기 — 버튼 모양은 호출자(스케치 버튼 클래스)가 정한다. */
export function ExcerptToggle({
  sourceId,
  locator,
  preloaded,
  label,
  className,
}: {
  sourceId?: string | null;
  locator?: string | null;
  preloaded?: SVExcerpt | null;
  label: string;
  className: string;
}) {
  const [open, setOpen] = useState(false);
  const [excerpt, setExcerpt] = useState<SVExcerpt | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (!sourceId || !locator) return null;

  const toggle = async () => {
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    if (excerpt) return;
    if (preloaded?.text && preloaded.locator === locator && (!preloaded.source_id || preloaded.source_id === sourceId)) {
      setExcerpt(preloaded);
      return;
    }
    setLoading(true);
    setErr(null);
    try {
      const ex = await fetchExcerpt(sourceId, locator);
      setExcerpt({ ...ex, locator: ex.locator ?? locator });
    } catch {
      setErr('발췌를 불러오지 못했습니다.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <button type="button" className={className} onClick={toggle} aria-expanded={open}>
        {open ? '발췌 닫기' : label}
      </button>
      {open && loading && <div className="svx-excerpt">발췌 불러오는 중…</div>}
      {open && err && <div className="svx-err">{err}</div>}
      {open && excerpt && <ExcerptBox excerpt={excerpt} />}
    </>
  );
}

