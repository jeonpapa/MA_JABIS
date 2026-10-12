import { useEffect, useRef, useState } from 'react';
import {
  clampExcerpt,
  EXCERPT_MAX_CHARS,
  fetchExcerpt,
  fetchStatement,
  safeHttpUrl,
  sourceTypeLabel,
  TOPIC_LABELS,
  type SVExcerpt,
  type SVProvenance,
  type SVQuote,
  type SVStatementDetail,
} from '@/api/stakeholder';
import { GradeBadge, IntegrityBadge, LocatorMatchBadge } from './GradeBadge';
import PersonAvatar from './PersonAvatar';

// 발언 블록 상세 — 타임라인 드로어와 워크벤치 우측 패널이 같은 본문을 쓴다.
//
// 증거 표시 규칙 (agents/rules/stakeholder_verbatim_rules.md §2-1, §4.2, §5):
//   1) verbatim(인용)은 저장된 문자열 그대로(가공·따옴표 제거 없음), summary·analysis 와 시각적으로 분리.
//   2) 인용마다 증거등급 · 출처 링크 · 무결성(integrity_status) · 원문 대조(locator_match) 배지.
//      locator_match==='none' 은 숨기지 않고 "원문 불일치" 경고로 드러낸다.
//   3) 원문은 locator 창 발췌(≤600자)만 — 전문 링크 없음.

const DEFAULT_COPYRIGHT =
  '원문 저작권은 각 발행기관·언론사에 있습니다. 내부 검증과 출처(provenance) 보존 목적으로만 발췌를 제공합니다.';

function fmtDate(s?: string | null): string {
  if (!s) return '—';
  return s.slice(0, 10).replace(/-/g, '.');
}

function fmtDateTime(s?: string | null): string {
  if (!s) return '—';
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? s : d.toLocaleString('ko-KR');
}

function shortSha(s?: string | null): string {
  if (!s) return '—';
  return s.length > 16 ? `${s.slice(0, 12)}…${s.slice(-4)}` : s;
}

function SourceLink({ prov, fallbackUrl }: { prov?: SVProvenance | null; fallbackUrl?: string | null }) {
  const href = safeHttpUrl(prov?.url) ?? safeHttpUrl(fallbackUrl);
  const label = prov?.title || prov?.source_id || '원문 출처';
  if (!href) {
    return <span className="text-[11px] text-[#8B9BB4]">{label}</span>;
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1 text-[11px] text-[#00E5CC] hover:underline min-w-0"
      title={href}
      onClick={e => e.stopPropagation()}
    >
      <i className="ri-external-link-line flex-shrink-0"></i>
      <span className="truncate">{label}</span>
    </a>
  );
}

function ExcerptBox({ excerpt }: { excerpt: SVExcerpt }) {
  const { text, clamped } = clampExcerpt(excerpt.text);
  return (
    <div className="mt-2 rounded-lg border border-[#2A3545] bg-[#0A0E1A] p-3">
      <p className="text-[10px] font-semibold text-[#4A5568] mb-1.5">
        원문 발췌 · locator 창 (최대 {EXCERPT_MAX_CHARS}자)
        {excerpt.locator && <code className="ml-1.5 font-mono text-[#8B9BB4]">{excerpt.locator}</code>}
      </p>
      <p className="text-[12px] leading-relaxed text-[#C9D1DC] whitespace-pre-wrap break-words">
        {text}
        {clamped && <span className="text-[#4A5568]"> … (발췌 상한 {EXCERPT_MAX_CHARS}자)</span>}
      </p>
    </div>
  );
}

/** 발췌 토글 버튼 + 패널. 미리 받은 발췌(detail.excerpt)가 같은 locator 면 재요청하지 않는다. */
function ExcerptToggle({
  sourceId,
  locator,
  preloaded,
  label = '원문 발췌 보기',
}: {
  sourceId?: string | null;
  locator?: string | null;
  preloaded?: SVExcerpt | null;
  label?: string;
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
    if (
      preloaded &&
      preloaded.text &&
      preloaded.locator === locator &&
      (!preloaded.source_id || preloaded.source_id === sourceId)
    ) {
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
    <div>
      <button
        onClick={toggle}
        className="inline-flex items-center gap-1 text-[11px] font-semibold text-[#8B9BB4] hover:text-white cursor-pointer"
        aria-expanded={open}
      >
        <i className={open ? 'ri-arrow-up-s-line' : 'ri-file-text-line'}></i>
        {open ? '발췌 닫기' : label}
      </button>
      {open && loading && (
        <p className="mt-2 text-[11px] text-[#8B9BB4]">
          <i className="ri-loader-4-line animate-spin mr-1"></i>발췌 불러오는 중…
        </p>
      )}
      {open && err && <p className="mt-2 text-[11px] text-red-400">{err}</p>}
      {open && excerpt && <ExcerptBox excerpt={excerpt} />}
    </div>
  );
}

function QuoteCard({
  quote,
  prov,
  statementGrade,
  statementGradeClass,
  preloaded,
}: {
  quote: SVQuote;
  prov?: SVProvenance | null;
  statementGrade?: string | null;
  statementGradeClass?: string | null;
  preloaded?: SVExcerpt | null;
}) {
  const mismatch = (quote.locator_match || '').toLowerCase() === 'none';
  const grade = quote.evidence_grade || statementGrade;
  return (
    <div
      className={`rounded-xl border p-3.5 ${
        mismatch ? 'border-red-500/50 bg-red-500/5' : 'border-[#1E2530] bg-[#0D1117]'
      }`}
    >
      {mismatch && (
        <p className="mb-2 flex items-start gap-1.5 text-[11px] font-semibold text-red-400">
          <i className="ri-error-warning-fill mt-px"></i>
          원문 불일치 — 이 인용 문구를 근거 원문에서 찾지 못했습니다. 인용·보고 전 원문 확인이 필요합니다.
        </p>
      )}
      {/* verbatim: 저장된 그대로. 가공 금지. */}
      <blockquote
        className={`border-l-[3px] pl-3 font-serif text-[14px] leading-relaxed text-white whitespace-pre-wrap break-words ${
          mismatch ? 'border-red-400' : 'border-[#00E5CC]'
        }`}
        data-verbatim="true"
      >
        {quote.quote_text}
      </blockquote>
      <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
        <GradeBadge grade={grade} gradeClass={quote.evidence_grade ? null : statementGradeClass} compact />
        <LocatorMatchBadge match={quote.locator_match} />
        <IntegrityBadge status={prov?.integrity_status} />
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 min-w-0">
        <SourceLink prov={prov} />
        {quote.locator && (
          <code className="text-[10px] font-mono text-[#4A5568] break-all" title="인용 위치 (source_locator)">
            {quote.locator}
          </code>
        )}
      </div>
      <div className="mt-1.5">
        <ExcerptToggle sourceId={quote.source_id || prov?.source_id} locator={quote.locator} preloaded={preloaded} />
      </div>
    </div>
  );
}

function provenanceFor(quote: SVQuote, provenance: SVProvenance[]): SVProvenance | null {
  if (quote.source_id) {
    const hit = provenance.find(p => p.source_id === quote.source_id);
    if (hit) return hit;
  }
  // 인용별 source_id 가 없으면 발언 블록의 첫 출처로 폴백 (단일 출처 블록이 대부분)
  return provenance[0] ?? null;
}

export function StatementDetailPanel({
  statementId,
  copyrightNotice,
  variant = 'drawer',
  topicLabels = TOPIC_LABELS,
}: {
  statementId: string;
  copyrightNotice?: string | null;
  variant?: 'drawer' | 'workbench';
  topicLabels?: Record<string, string>;
}) {
  const [detail, setDetail] = useState<SVStatementDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setDetail(null);
    fetchStatement(statementId)
      .then(d => {
        if (!cancelled) setDetail(d);
      })
      .catch(() => {
        if (!cancelled) setError('발언 상세를 불러오지 못했습니다.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [statementId, tick]);

  if (loading) {
    return (
      <div className="space-y-3 animate-pulse" aria-busy="true">
        <div className="h-5 w-1/2 rounded bg-[#1E2530]" />
        <div className="h-20 rounded-xl bg-[#1E2530]" />
        <div className="h-4 w-3/4 rounded bg-[#1E2530]" />
        <div className="h-4 w-2/3 rounded bg-[#1E2530]" />
      </div>
    );
  }
  if (error || !detail) {
    return (
      <div className="py-10 text-center">
        <p className="text-sm text-red-400">{error || '불러오지 못했습니다.'}</p>
        <button onClick={() => setTick(t => t + 1)} className="mt-3 text-sm text-[#00E5CC] hover:underline cursor-pointer">
          다시 시도
        </button>
      </div>
    );
  }

  const provenance = detail.provenance ?? [];
  const quotes = detail.quotes ?? [];
  const eventTitle = detail.event?.title ?? detail.event_title;
  const eventDate = detail.event?.event_date ?? detail.event_date;
  const personId = detail.person?.person_id ?? detail.person_id;
  const hasPhoto = Boolean(detail.person?.photo);
  const isWb = variant === 'workbench';
  const firstQuote = quotes[0];
  const excerptSourceId = detail.excerpt?.source_id ?? firstQuote?.source_id ?? provenance[0]?.source_id;
  const excerptLocator = detail.excerpt?.locator ?? firstQuote?.locator;
  const roleCheck = safeHttpUrl(detail.role_check_url);

  return (
    <div className="space-y-5">
      {/* 인물 헤더 */}
      <div className="flex items-start gap-3">
        <PersonAvatar name={detail.speaker_name} personId={personId} hasPhoto={hasPhoto} size="lg" />
        <div className="min-w-0 flex-1">
          <h2 className="text-white font-bold text-lg leading-tight">{detail.speaker_name || '발언자 미상'}</h2>
          <p className="text-[#8B9BB4] text-xs mt-0.5">{detail.speaker_role_at_event || '당시 직책 미기재'}</p>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <GradeBadge grade={detail.evidence_grade} gradeClass={detail.grade_class} />
            {detail.topic && (
              <span className="rounded bg-[#1E2530] px-1.5 py-0.5 text-[10px] text-[#8B9BB4]">
                {topicLabels[detail.topic] ?? detail.topic}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* verbatim */}
      <section>
        <p className="mb-2 flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wider text-[#00E5CC]">
          <i className="ri-double-quotes-l"></i>원문 인용 · verbatim
          <span className="font-normal normal-case tracking-normal text-[#4A5568]">— 원문 그대로, 교정 없음</span>
        </p>
        {quotes.length > 0 ? (
          <div className="space-y-2.5">
            {quotes.map(q => (
              <QuoteCard
                key={q.quote_id}
                quote={q}
                prov={provenanceFor(q, provenance)}
                statementGrade={detail.evidence_grade}
                statementGradeClass={detail.grade_class}
                preloaded={detail.excerpt}
              />
            ))}
          </div>
        ) : (
          <p className="rounded-xl border border-dashed border-[#2A3545] px-3 py-3 text-[12px] text-[#8B9BB4]">
            확보된 직접인용이 없습니다. 아래 내용은 기자·기관의 귀속요약이며 발언 원문이 아닙니다.
          </p>
        )}
      </section>

      {/* summary — 인용 아님 */}
      {detail.summary_text && (
        <section className="rounded-xl bg-[#1E2530]/60 p-3.5">
          <p className="mb-1.5 text-[11px] font-bold text-[#8B9BB4]">
            귀속요약 · summary <span className="font-normal text-[#4A5568]">— 기자·기관 요약, 인용 아님</span>
          </p>
          <p className="text-[13px] leading-relaxed text-[#C9D1DC] whitespace-pre-wrap">{detail.summary_text}</p>
        </section>
      )}

      {/* analysis — 해석 */}
      {detail.analysis_text && (
        <section className="rounded-xl border border-[#8B5CF6]/30 bg-[#8B5CF6]/10 p-3.5">
          <p className="mb-1.5 text-[11px] font-bold text-[#A78BFA]">
            {isWb ? '정책적 의미' : '판단'} · analysis{' '}
            <span className="font-normal text-[#7C6BA8]">— 분석가 해석, 발언 아님</span>
          </p>
          <p className="text-[13px] leading-relaxed text-[#DDD6FE] whitespace-pre-wrap">{detail.analysis_text}</p>
        </section>
      )}

      {(detail.limitation_text || detail.policy_status) && (
        <section className="rounded-xl border border-[#F59E0B]/30 bg-[#F59E0B]/10 p-3.5 space-y-2">
          {detail.limitation_text && (
            <div>
              <p className="mb-1 text-[11px] font-bold text-[#F59E0B]">{isWb ? '해석 한계' : '한계'}</p>
              <p className="text-[13px] leading-relaxed text-[#FDE68A] whitespace-pre-wrap">{detail.limitation_text}</p>
            </div>
          )}
          {detail.policy_status && (
            <div>
              <p className="mb-1 text-[11px] font-bold text-[#F59E0B]">제도상태</p>
              <p className="text-[13px] leading-relaxed text-[#FDE68A] whitespace-pre-wrap">{detail.policy_status}</p>
            </div>
          )}
        </section>
      )}

      {/* 메타 */}
      <section>
        <dl className="grid grid-cols-[88px_1fr] gap-x-3 gap-y-1.5 text-[12px]">
          <dt className="text-[#4A5568]">행사</dt>
          <dd className="text-white">{eventTitle || '—'}</dd>
          <dt className="text-[#4A5568]">행사일</dt>
          <dd className="text-white">{fmtDate(eventDate)}</dd>
          <dt className="text-[#4A5568]">게시일</dt>
          <dd className="text-white">{fmtDate(detail.publication_date ?? provenance[0]?.publication_date)}</dd>
          <dt className="text-[#4A5568]">당시 직책</dt>
          <dd className="text-white">
            {detail.speaker_role_at_event || '—'}
            {roleCheck && (
              <a
                href={roleCheck}
                target="_blank"
                rel="noopener noreferrer"
                className="ml-2 text-[11px] text-[#00E5CC] hover:underline"
              >
                직책 확인 출처 <i className="ri-external-link-line"></i>
              </a>
            )}
          </dd>
        </dl>
      </section>

      {/* 출처 감사 */}
      <section>
        <p className="mb-2 text-[11px] font-bold uppercase tracking-wider text-[#8B9BB4]">
          {isWb ? '증거·출처' : '출처 감사'}
        </p>
        {provenance.length === 0 && (
          <p className="text-[12px] text-[#8B9BB4]">등록된 출처가 없습니다.</p>
        )}
        <div className="space-y-2">
          {provenance.map(p => {
            const live = p.last_live_check;
            return (
              <div key={p.source_id} className="rounded-xl border border-[#1E2530] bg-[#0D1117] p-3 space-y-1.5">
                <div className="flex flex-wrap items-center gap-1.5">
                  <IntegrityBadge status={p.integrity_status} />
                  <GradeBadge grade={p.evidence_grade} compact />
                  <span className="text-[10px] text-[#8B9BB4]">{sourceTypeLabel(p.source_type)}</span>
                </div>
                <SourceLink prov={p} />
                <dl className="grid grid-cols-[88px_1fr] gap-x-3 gap-y-0.5 text-[11px]">
                  <dt className="text-[#4A5568]">게시일</dt>
                  <dd className="text-[#C9D1DC]">{fmtDate(p.publication_date)}</dd>
                  <dt className="text-[#4A5568]">출처 ID</dt>
                  <dd className="text-[#C9D1DC] font-mono break-all">{p.source_id}</dd>
                  <dt className="text-[#4A5568]">원문 SHA-256</dt>
                  <dd className="text-[#C9D1DC] font-mono" title={p.sha256 || ''}>{shortSha(p.sha256)}</dd>
                  {p.text_sha256 && (
                    <>
                      <dt className="text-[#4A5568]">텍스트 SHA-256</dt>
                      <dd className="text-[#C9D1DC] font-mono" title={p.text_sha256}>{shortSha(p.text_sha256)}</dd>
                    </>
                  )}
                  {live && (
                    <>
                      <dt className="text-[#4A5568]">라이브 확인</dt>
                      <dd className="text-[#C9D1DC]">
                        {fmtDateTime(live.checked_at)}
                        {live.http_status != null && ` · HTTP ${live.http_status}`}
                      </dd>
                    </>
                  )}
                </dl>
              </div>
            );
          })}
        </div>
        <div className="mt-3">
          <ExcerptToggle
            sourceId={excerptSourceId}
            locator={excerptLocator}
            preloaded={detail.excerpt}
            label={isWb ? '원문 및 인용 위치 열기' : '원문 발췌 보기'}
          />
        </div>
      </section>

      <p className="border-t border-[#1E2530] pt-3 text-[10px] leading-relaxed text-[#4A5568]">
        <i className="ri-copyright-line mr-1"></i>
        {copyrightNotice || DEFAULT_COPYRIGHT}
      </p>
    </div>
  );
}

export default function StatementDrawer({
  statementId,
  copyrightNotice,
  topicLabels,
  onClose,
}: {
  statementId: string;
  copyrightNotice?: string | null;
  topicLabels?: Record<string, string>;
  onClose: () => void;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
  }, [statementId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-[60] flex justify-end" role="dialog" aria-modal="true" aria-label="발언 상세">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <aside className="relative h-full w-full max-w-xl overflow-y-auto border-l border-[#1E2530] bg-[#161B27] px-6 py-5 shadow-2xl">
        <div className="mb-4 flex items-center justify-between">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-[#4A5568]">발언 상세</span>
          <button
            ref={closeRef}
            onClick={onClose}
            className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-[#8B9BB4] hover:bg-[#1E2530] hover:text-white cursor-pointer"
          >
            닫기 <i className="ri-close-line"></i>
          </button>
        </div>
        <StatementDetailPanel
          statementId={statementId}
          copyrightNotice={copyrightNotice}
          topicLabels={topicLabels}
          variant="drawer"
        />
      </aside>
    </div>
  );
}
