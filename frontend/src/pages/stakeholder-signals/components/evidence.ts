import type { SVProvenance, SVQuote } from '@/api/stakeholder';

// 증거 표시 순수 헬퍼(컴포넌트 파일과 분리 — react-refresh 규칙).

export const MATCH_META: Record<string, { label: string; tone: string; title: string }> = {
  range: { label: '원문 위치 일치', tone: 'ok', title: '인용 문구가 근거 locator 줄 범위 안에서 그대로 확인됨' },
  file: { label: '원문 내 일치(위치 상이)', tone: 'info', title: '인용 문구가 원문 파일에는 있으나 locator 줄 범위 밖에서 확인됨' },
  none: { label: '원문 불일치', tone: 'bad', title: '인용 문구를 근거 원문에서 찾지 못함 — 인용 사용 전 원문 확인 필요' },
  unchecked: { label: '원문 대조 전', tone: 'neutral', title: 'locator 가 없거나 아직 대조하지 않음' },
};

/** 정책 시그널 근거(claim 은 요약) — locator_match 는 문구 대조가 아니라 근거 위치 유효성 검사. */
export const EVIDENCE_LOCATOR_META: Record<string, { label: string; tone: string; title: string }> = {
  range: { label: '근거 위치 확인', tone: 'ok', title: '근거 locator 줄 범위가 원문에서 유효함(요약 claim 이라 문구 대조는 아님)' },
  file: { label: '근거 파일 내 확인(위치 상이)', tone: 'info', title: '근거 원문 파일은 있으나 locator 줄 범위가 맞지 않음' },
  none: { label: '원문 불일치', tone: 'bad', title: '근거 locator 를 원문에서 확인하지 못함 — 근거 사용 전 원문 확인 필요' },
  unchecked: { label: '근거 대조 전', tone: 'neutral', title: 'locator 가 없거나 아직 확인하지 않음' },
};

export const INTEGRITY_META: Record<string, { label: string; tone: string; title: string }> = {
  verified: { label: '원문 대조 완료', tone: 'ok', title: 'SHA-256 일치 — sync 가 원문 파일 SHA-256 을 재계산해 manifest 와 일치함을 확인' },
  mismatch: { label: '원문 대조 불일치', tone: 'bad', title: 'SHA-256 불일치 — 원문 파일 SHA-256 이 manifest 와 다름(변조·손상 가능성)' },
  missing: { label: '원문 파일 없음', tone: 'bad', title: 'manifest 에 경로가 있으나 파일이 없음' },
  excluded: { label: '의도적 제외', tone: 'neutral', title: 'manifest 에 있으나 sync 허용목록 밖이라 의도적으로 받지 않은 출처 — 실패가 아님' },
  no_local: { label: '로컬 원문 없음', tone: 'neutral', title: '로컬 사본이 없는 출처(예: 영상) — URL 로만 확인' },
  unchecked: { label: '무결성 미검증', tone: 'neutral', title: '아직 무결성 검사를 하지 않음' },
};

export function integrityText(status?: string | null): string {
  const s = (status || 'unchecked').toLowerCase();
  return (INTEGRITY_META[s] ?? INTEGRITY_META.unchecked).label;
}

/** 무결성 툴팁 — 사용자 라벨은 '원문 대조 완료', 해시·기술 설명은 title 로만. */
export function integrityTitle(status?: string | null, hash?: string | null): string {
  const m = INTEGRITY_META[(status || 'unchecked').toLowerCase()] ?? INTEGRITY_META.unchecked;
  return hash ? `${m.title}\nSHA-256: ${hash}` : m.title;
}

/** 증거등급 사용자 라벨 — 'C-quote/C-summary' → '언론 직접인용 / 언론 요약'. 코드는 툴팁으로. */
export function gradeTitle(grade?: string | null): string {
  return grade ? `증거등급 코드: ${grade}` : '증거등급 미표기';
}

export function isMismatch(match?: string | null): boolean {
  return (match || '').toLowerCase() === 'none';
}

export function provenanceFor(quote: SVQuote, provenance: SVProvenance[]): SVProvenance | null {
  if (quote.source_id) {
    const hit = provenance.find(p => p.source_id === quote.source_id);
    if (hit) return hit;
  }
  return provenance[0] ?? null;
}
