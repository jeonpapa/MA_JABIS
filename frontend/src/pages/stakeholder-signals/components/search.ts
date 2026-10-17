import type { SVPolicySignal, SVStatementRow } from '@/api/stakeholder';

// 워크벤치 검색 — 공백 무시 부분일치 + 한국어 복합어 변형(약가유연계약제 ⊃ 유연계약).
// 백엔드 store.search_variants() 와 같은 규칙(접두 '약가' · 접미 '제도/제/안' 제거, 2자 미만 변형 금지).

const PREFIXES = ['약가'];
const SUFFIXES = ['제도', '제', '안'];

export function normalizeSearch(s?: string | null): string {
  return (s || '').normalize('NFC').toLowerCase().replace(/\s+/g, '');
}

export function searchVariants(q?: string | null): string[] {
  const base = normalizeSearch(q);
  if (!base) return [];
  const stems = [base];
  PREFIXES.forEach(p => {
    if (base.startsWith(p) && base.length - p.length >= 2) stems.push(base.slice(p.length));
  });
  const out: string[] = [base];
  stems.forEach(st => {
    out.push(st);
    SUFFIXES.forEach(suf => {
      if (st.endsWith(suf) && st.length - suf.length >= 2) out.push(st.slice(0, -suf.length));
    });
  });
  return [...new Set(out)];
}

export function matchesVariants(hay: string, variants: string[]): boolean {
  if (!variants.length) return true;
  const h = normalizeSearch(hay);
  return variants.some(v => h.includes(v));
}

/** 시그널 본문(제목·논지·근거 claim·분석 추론)이 검색어와 맞으면, 그 근거와 같은 출처·같은 인물의 발언 id 를 돌려준다. */
export function signalLinkedStatementIds(
  signals: SVPolicySignal[],
  rows: SVStatementRow[],
  variants: string[],
): { ids: Set<string>; signalTitles: string[] } {
  const ids = new Set<string>();
  const titles: string[] = [];
  if (!variants.length) return { ids, signalTitles: titles };
  signals.forEach(sig => {
    const ev = sig.confirmed_evidence ?? [];
    const hay = [
      sig.title,
      sig.thesis,
      sig.not_confirmed,
      sig.analyst_inference?.likely_contract_architecture,
      sig.analyst_inference?.most_likely_first_use,
      ...ev.map(e => e.claim),
      ...(sig.watch_triggers ?? []),
    ].join(' ');
    if (!matchesVariants(hay, variants)) return;
    titles.push(sig.title);
    ev.forEach(e => {
      rows.forEach(r => {
        const sameSource = !!e.source_id && r.source?.source_id === e.source_id;
        const samePerson = !!e.person && (r.speaker_name === e.person || r.person?.name === e.person);
        if (sameSource && samePerson) ids.add(r.statement_id);
      });
    });
  });
  return { ids, signalTitles: titles };
}
