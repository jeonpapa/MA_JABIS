import { useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import { gradeLabel, safeHttpUrl, type SVStatementDetail } from '@/api/stakeholder';
import { fmtDot, gradeCls } from '../sketch/helpers';
import { gradeTitle, integrityText, integrityTitle, isMismatch, provenanceFor } from './evidence';
import { ExcerptToggle, LocatorDisclosure, MismatchWarning, QuoteMarks } from './statementDetail';
import { useStatementDetail } from './useStatementDetail';

// 스케치 001 aside.drawer — 우측 455px 슬라이드(스크림 없음), "닫기 ×", 이름 h2, 등급 배지, blockquote,
// .meta(행사·행사일·당시 직책·판단·한계), .source(출처 감사). 스케치에 자리가 없는 하드 제약 요소
// (인용별 배지·원문 불일치·귀속요약 라벨·≤600자 발췌·워크벤치 진입)는 같은 시각 언어로 덧붙인다.

const DEFAULT_COPYRIGHT = '원문 저작권은 각 발행기관·언론사에 있습니다. 내부 검증과 출처(provenance) 보존 목적으로만 발췌를 제공합니다.';

function DrawerBody({ detail, copyrightNotice }: { detail: SVStatementDetail; copyrightNotice?: string | null }) {
  const provenance = detail.provenance ?? [];
  const quotes = detail.quotes ?? [];
  const eventTitle = detail.event?.title ?? detail.event_title;
  const eventDate = detail.event?.event_date ?? detail.event_date;
  const cls = gradeCls(detail.evidence_grade, detail.grade_class);
  const roleCheck = safeHttpUrl(detail.role_check_url);
  return (
    <>
      <h2>{detail.speaker_name || '발언자 미상'}</h2>
      <div className={`badge ${cls === 'role' ? 'role' : cls}`} title={gradeTitle(detail.evidence_grade)}>
        {gradeLabel(detail.evidence_grade)}
      </div>

      {quotes.length > 0 ? (
        <>
          <div className="svx-label drawer-label">
            원문 인용 · verbatim <i>— 원문 그대로, 교정 없음</i>
          </div>
          {quotes.map(q => {
            const prov = provenanceFor(q, provenance);
            return (
              <div key={q.quote_id}>
                <blockquote className={isMismatch(q.locator_match) ? 'mismatch' : undefined} data-verbatim="true">
                  {q.quote_text}
                </blockquote>
                <div className="q-marks">
                  <MismatchWarning match={q.locator_match} />
                  <QuoteMarks quote={q} prov={prov} statementGrade={detail.evidence_grade} statementGradeClass={detail.grade_class} />
                  <LocatorDisclosure locator={q.locator} />
                </div>
              </div>
            );
          })}
        </>
      ) : (
        <p className="no-quote">확보된 직접인용이 없습니다. 아래 귀속요약은 기자·기관의 요약이며 발언 원문이 아닙니다.</p>
      )}

      <div className="meta">
        <span>행사</span>
        <b>{eventTitle || '—'}</b>
        <span>행사일</span>
        <b>{fmtDot(eventDate)}</b>
        <span>당시 직책</span>
        <b>
          {detail.speaker_role_at_event || '—'}
          {roleCheck && (
            <a className="svx-link role-check" href={roleCheck} target="_blank" rel="noopener noreferrer">
              직책 확인 출처 ↗
            </a>
          )}
        </b>
        {detail.summary_text && (
          <>
            <span>귀속요약</span>
            <b>
              {detail.summary_text}
              <i className="note">summary — 기자·기관 요약, 인용 아님</i>
            </b>
          </>
        )}
        <span>판단</span>
        <b>
          {detail.analysis_text || '—'}
          {detail.analysis_text && <i className="note">analysis — 분석가 해석, 발언 아님</i>}
        </b>
        <span>한계</span>
        <b>{detail.limitation_text || '—'}</b>
        {detail.policy_status && (
          <>
            <span>제도상태</span>
            <b>{detail.policy_status}</b>
          </>
        )}
      </div>

      {provenance.length === 0 && (
        <div className="source">
          <b>출처 감사</b>
          <br />
          등록된 출처가 없습니다.
        </div>
      )}
      {provenance.map(p => {
        const href = safeHttpUrl(p.url);
        const q = quotes.find(x => x.source_id === p.source_id) ?? quotes[0];
        const locator = q?.locator ?? (detail.excerpt?.source_id === p.source_id ? detail.excerpt?.locator : null);
        return (
          <div className="source" key={p.source_id}>
            <b>출처 감사</b>
            <br />
            {href ? (
              <a className="svx-link" href={href} target="_blank" rel="noopener noreferrer">
                {p.title || p.source_id}
              </a>
            ) : (
              p.title || p.source_id
            )}
            , {fmtDot(p.publication_date ?? detail.publication_date)}
            <br />
            <span title={integrityTitle(p.integrity_status, p.text_sha256 || p.sha256)}>{integrityText(p.integrity_status)}</span>
            <LocatorDisclosure locator={locator} />
            <div className="src-excerpt">
              <ExcerptToggle
                sourceId={p.source_id}
                locator={locator}
                preloaded={detail.excerpt}
                label="원문 발췌 보기 (≤600자)"
                className="svx-linkbtn"
              />
            </div>
          </div>
        );
      })}

      <p className="wb-link">
        <Link className="svx-link" to={`/stakeholder-signals/workbench?statement=${encodeURIComponent(detail.statement_id)}`}>
          애널리스트 워크벤치에서 열기 ›
        </Link>
      </p>
      <p className="copy">{copyrightNotice || DEFAULT_COPYRIGHT}</p>
    </>
  );
}

export default function TimelineDrawer({
  statementId,
  copyrightNotice,
  onClose,
}: {
  statementId: string | null;
  copyrightNotice?: string | null;
  onClose: () => void;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (statementId) closeRef.current?.focus();
  }, [statementId]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <aside className={`drawer${statementId ? ' open' : ''}`} aria-hidden={!statementId} aria-label="발언 상세">
      <button ref={closeRef} className="close" onClick={onClose} tabIndex={statementId ? 0 : -1}>
        닫기 ×
      </button>
      {statementId && <DrawerContent statementId={statementId} copyrightNotice={copyrightNotice} />}
    </aside>
  );
}

function DrawerContent({ statementId, copyrightNotice }: { statementId: string; copyrightNotice?: string | null }) {
  const { detail, loading, error, retry } = useStatementDetail(statementId);
  if (loading) return <h2 className="loading">불러오는 중…</h2>;
  if (error || !detail)
    return (
      <>
        <h2>발언 상세</h2>
        <div className="svx-err">
          {error || '불러오지 못했습니다.'}{' '}
          <button type="button" className="svx-linkbtn" onClick={retry}>
            다시 시도
          </button>
        </div>
      </>
    );
  return <DrawerBody detail={detail} copyrightNotice={copyrightNotice} />;
}
