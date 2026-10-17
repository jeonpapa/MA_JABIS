import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  fetchOverview,
  fetchSignals,
  fetchStatements,
  gradeClassOf,
  gradeLabel,
  headlineQuoteOf,
  ORG_FILTERS,
  safeHttpUrl,
  sourceTypeLabel,
  TOPIC_LABELS,
  TOPIC_ORDER,
  type SVGradeClass,
  type SVOverview,
  type SVPolicySignal,
  type SVStatementDetail,
  type SVStatementRow,
} from '@/api/stakeholder';
import { getCurrentUser, isAdmin } from '@/utils/authUsers';
import PersonAvatar from './components/PersonAvatar';
import { LocatorMatchBadge } from './components/GradeBadge';
import { gradeTitle, integrityText, integrityTitle, isMismatch, provenanceFor } from './components/evidence';
import { matchesVariants, searchVariants, signalLinkedStatementIds } from './components/search';
import { ExcerptToggle, MismatchWarning, QuoteMarks } from './components/statementDetail';
import { useStatementDetail } from './components/useStatementDetail';
import RelationshipMapModal from './components/RelationshipMapModal';
import { fmtDot, PREPARING, TOPIC_FILTER_LABELS } from './sketch/helpers';
import './sketch/base.css';
import './sketch/common.css';
import './sketch/s002.css';
import './sketch/s002.additions.css';

// Stakeholder Signals — 애널리스트 워크벤치 = 스케치 002 그대로.
//   흰 상단 바(로고·검색·"검증 큐") / 좌 248px 필터 · 중앙 #fafafa 주제 레인 · 우 390px 상세.
//   "검증 큐" 는 보류 기능 — 모양 그대로 비활성(title "준비 중"). 검색창은 스트림을 거르는 실제 입력.
//   보기 탭: 스트림(현재) · 인물(관리자 → 모니터링 인물 관리) · 관계도(관계도 모달).
//   1050px 이하에서 스케치는 상세를 숨긴다 — 그때는 발언을 누르면 같은 상세를 모달로 연다.

const OTHER_ORG = '__other__';
const GRADE_OPTIONS: { key: SVGradeClass; label: string; defaultOn: boolean }[] = [
  { key: 'a', label: '공식 원문 A', defaultOn: true },
  { key: 'b', label: '기관 인용 B', defaultOn: true },
  { key: 'c', label: '언론 인용 C', defaultOn: true },
  { key: 'role', label: '후보만 확인', defaultOn: false },
];
type PeriodKey = '3m' | '6m' | '1y' | 'all';
const PERIOD_OPTIONS: { key: PeriodKey; label: string; months: number | null }[] = [
  { key: '3m', label: '최근 3개월', months: 3 },
  { key: '6m', label: '최근 6개월', months: 6 },
  { key: '1y', label: '최근 1년', months: 12 },
  { key: 'all', label: '전체', months: null },
];

/** 오늘(KST) YYYY-MM-DD */
function todayKst(): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
}

/** end(YYYY-MM-DD) 에서 n개월 전 + 1일 */
function monthsBefore(end: string, n: number): string {
  const [y, m, d] = end.split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1 - n, d + 1));
  return dt.toISOString().slice(0, 10);
}

const DEFAULT_COPYRIGHT = '원문 저작권은 각 발행기관·언론사에 있습니다. 내부 검증과 출처(provenance) 보존 목적으로만 발췌를 제공합니다.';

function firstSentence(s?: string | null): string {
  const t = (s || '').trim();
  const m = t.match(/^(.+?[.!?。])(\s|$)/);
  return (m ? m[1] : t).slice(0, 160);
}

function useIsWide(): boolean {
  const q = '(min-width: 1051px)';
  const [ok, setOk] = useState(() => (typeof window !== 'undefined' ? window.matchMedia(q).matches : true));
  useEffect(() => {
    const mq = window.matchMedia(q);
    const on = () => setOk(mq.matches);
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, []);
  return ok;
}

interface WbData {
  rows: SVStatementRow[];
  orgsById: Record<string, string[]>;
}

async function loadWorkbench(): Promise<WbData> {
  const [all, ...perOrg] = await Promise.all([fetchStatements(), ...ORG_FILTERS.map(o => fetchStatements({ org: o.key }))]);
  const orgsById: Record<string, string[]> = {};
  perOrg.forEach((items, i) => items.forEach(r => (orgsById[r.statement_id] ||= []).push(ORG_FILTERS[i].key)));
  all.forEach(r => {
    if (!orgsById[r.statement_id]) orgsById[r.statement_id] = [OTHER_ORG];
  });
  return { rows: all, orgsById };
}

function toggle(set: Set<string>, k: string): Set<string> {
  const n = new Set(set);
  if (n.has(k)) n.delete(k);
  else n.add(k);
  return n;
}

function DetailBody({ detail, copyrightNotice }: { detail: SVStatementDetail; copyrightNotice?: string | null }) {
  const provenance = detail.provenance ?? [];
  const quotes = detail.quotes ?? [];
  const first = provenance[0];
  const firstQuote = quotes[0];
  const href = safeHttpUrl(first?.url) ?? safeHttpUrl(detail.source_url);
  return (
    <>
      <div className="detail-top">
        <span>발언 상세</span>
        <span title={gradeTitle(detail.evidence_grade)}>{detail.evidence_grade ? gradeLabel(detail.evidence_grade) : '—'}</span>
      </div>
      <h2>{detail.event?.title ?? detail.event_title ?? '—'}</h2>
      <div className="person-card">
        <PersonAvatar
          name={detail.person?.name ?? detail.speaker_name}
          personId={detail.person?.person_id ?? detail.person_id}
          hasPhoto={detail.person?.photo}
          className="avatar"
        />
        <div>
          <b>{detail.speaker_name || '발언자 미상'}</b>
          <small>{detail.speaker_role_at_event || '당시 직책 미기재'}</small>
        </div>
      </div>

      {quotes.length > 0 ? (
        <>
          <div className="label">
            원문 인용 <i>verbatim — 원문 그대로, 교정 없음</i>
          </div>
          {quotes.map(q => (
            <div key={q.quote_id}>
              <div className={`quote${isMismatch(q.locator_match) ? ' mismatch' : ''}`} data-verbatim="true">
                {q.quote_text}
              </div>
              <div className="q-marks">
                <MismatchWarning match={q.locator_match} />
                <QuoteMarks quote={q} prov={provenanceFor(q, provenance)} statementGrade={detail.evidence_grade} statementGradeClass={detail.grade_class} />
              </div>
            </div>
          ))}
        </>
      ) : (
        <div className="no-quote">확보된 직접인용이 없습니다. 아래 귀속요약은 기자·기관의 요약이며 발언 원문이 아닙니다.</div>
      )}

      {detail.summary_text && (
        <>
          <div className="label">
            귀속요약 <i>summary — 기자·기관 요약, 인용 아님</i>
          </div>
          <div className="analysis">{detail.summary_text}</div>
        </>
      )}
      {detail.analysis_text && (
        <>
          <div className="label">
            정책적 의미 <i>analysis — 분석가 해석, 발언 아님</i>
          </div>
          <div className="analysis">{detail.analysis_text}</div>
        </>
      )}
      {(detail.limitation_text || detail.policy_status) && (
        <div className="warning">
          {detail.limitation_text && (
            <>
              <b>해석 한계</b>
              <br />
              <span>{detail.limitation_text}</span>
            </>
          )}
          {detail.policy_status && (
            <>
              {detail.limitation_text && <br />}
              <b>제도상태</b>
              <br />
              <span>{detail.policy_status}</span>
            </>
          )}
        </div>
      )}

      <div className="label">증거·출처</div>
      <div className="evidence">
        <div className="e-row">
          <span>행사일</span>
          <b>{fmtDot(detail.event?.event_date ?? detail.event_date)}</b>
        </div>
        <div className="e-row">
          <span>게시일</span>
          <b>{fmtDot(detail.publication_date ?? first?.publication_date)}</b>
        </div>
        <div className="e-row">
          <span>출처 유형</span>
          <b>{sourceTypeLabel(first?.source_type)}</b>
        </div>
        <div className="e-row">
          <span>무결성</span>
          <b title={first ? integrityTitle(first.integrity_status, first.text_sha256 || first.sha256) : undefined}>
            {first ? integrityText(first.integrity_status) : '—'}
          </b>
        </div>
        <div className="e-row">
          <span>출처</span>
          <b>
            {href ? (
              <a className="svx-link" href={href} target="_blank" rel="noopener noreferrer">
                {first?.title || first?.source_id || '원문 출처'}
              </a>
            ) : (
              first?.title || first?.source_id || '—'
            )}
          </b>
        </div>
      </div>
      <ExcerptToggle
        sourceId={detail.excerpt?.source_id ?? firstQuote?.source_id ?? first?.source_id}
        locator={detail.excerpt?.locator ?? firstQuote?.locator}
        preloaded={detail.excerpt}
        label="원문 및 인용 위치 열기 ↗"
        className="sourcebtn"
      />
      <p className="copy">{copyrightNotice || DEFAULT_COPYRIGHT}</p>
    </>
  );
}

function Detail({ statementId, copyrightNotice }: { statementId: string; copyrightNotice?: string | null }) {
  const { detail, loading, error, retry } = useStatementDetail(statementId);
  if (loading)
    return (
      <div className="detail-top">
        <span>발언 상세</span>
        <span>불러오는 중…</span>
      </div>
    );
  if (error || !detail)
    return (
      <div className="svx-err">
        {error || '불러오지 못했습니다.'}{' '}
        <button type="button" className="svx-linkbtn" onClick={retry}>
          다시 시도
        </button>
      </div>
    );
  return <DetailBody detail={detail} copyrightNotice={copyrightNotice} />;
}

export default function StakeholderWorkbenchPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [overview, setOverview] = useState<SVOverview | null>(null);
  const [data, setData] = useState<WbData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [query, setQuery] = useState('');
  const [period, setPeriod] = useState<PeriodKey>('all');
  const [signals, setSignals] = useState<SVPolicySignal[]>([]);
  const [serverHits, setServerHits] = useState<{ q: string; ids: Set<string> } | null>(null);
  const [orgs, setOrgs] = useState<Set<string>>(() => new Set([...ORG_FILTERS.map(o => o.key), OTHER_ORG]));
  const [topics, setTopics] = useState<Set<string>>(() => new Set(TOPIC_ORDER));
  const [grades, setGrades] = useState<Set<string>>(() => new Set(GRADE_OPTIONS.filter(g => g.defaultOn).map(g => g.key)));
  const [selectedId, setSelectedId] = useState<string | null>(() => params.get('statement'));
  const [modalOpen, setModalOpen] = useState(false);
  const [showMap, setShowMap] = useState(false);
  const isWide = useIsWide();
  const admin = isAdmin(getCurrentUser());

  useEffect(() => {
    let cancelled = false;
    fetchOverview()
      .then(o => !cancelled && setOverview(o))
      .catch(() => {/* 기간·검증 큐 수만 비운다 */});
    fetchSignals()
      .then(d => !cancelled && setSignals(d.signals))
      .catch(() => {/* 시그널 연결 검색만 빠진다 */});
    setError(null);
    loadWorkbench()
      .then(d => !cancelled && setData(d))
      .catch(() => !cancelled && setError('발언 목록을 불러오지 못했습니다.'));
    return () => {
      cancelled = true;
    };
  }, [tick]);

  const rows = useMemo(() => data?.rows ?? [], [data]);
  const orgCounts = useMemo(() => {
    const c: Record<string, number> = {};
    rows.forEach(r => (data?.orgsById[r.statement_id] ?? []).forEach(o => (c[o] = (c[o] ?? 0) + 1)));
    return c;
  }, [rows, data]);
  const orgOptions = useMemo(
    () => [...ORG_FILTERS, ...((orgCounts[OTHER_ORG] ?? 0) > 0 ? [{ key: OTHER_ORG, label: '기타(국회 등)' }] : [])],
    [orgCounts],
  );

  // 기간: 종료일 = 오늘(KST, 고정 scope.end 아님). 프리셋 최근 3/6개월·1년·전체(기본 — 수집 범위 시작일부터).
  const periodRange = useMemo(() => {
    const end = todayKst();
    const months = PERIOD_OPTIONS.find(p => p.key === period)?.months ?? null;
    return { start: months == null ? null : monthsBefore(end, months), end };
  }, [period]);

  // 검색: 서버(q — 인용문 전체·해석·한계까지, 공백 무시·복합어 변형) 결과와 화면 데이터 매칭을 합친다.
  const variants = useMemo(() => searchVariants(query), [query]);
  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setServerHits(null);
      return;
    }
    let cancelled = false;
    const t = window.setTimeout(() => {
      fetchStatements({ q })
        .then(items => !cancelled && setServerHits({ q, ids: new Set(items.map(r => r.statement_id)) }))
        .catch(() => {/* 서버 검색 실패 시 화면 매칭만 */});
    }, 300);
    return () => {
      cancelled = true;
      window.clearTimeout(t);
    };
  }, [query]);
  const topicTitles = useMemo(() => {
    const m: Record<string, string> = {};
    (overview?.topics ?? []).forEach(t => {
      if (t?.slug) m[t.slug] = t.title ?? '';
    });
    return m;
  }, [overview]);
  const signalLink = useMemo(() => signalLinkedStatementIds(signals, rows, variants), [signals, rows, variants]);

  const filtered = useMemo(() => {
    const serverIds = serverHits && serverHits.q === query.trim() ? serverHits.ids : null;
    return rows.filter(r => {
      if (!(data?.orgsById[r.statement_id] ?? [OTHER_ORG]).some(o => orgs.has(o))) return false;
      if (r.topic && !topics.has(r.topic)) return false;
      if (!grades.has(gradeClassOf(r.evidence_grade, r.grade_class))) return false;
      const d = (r.event?.event_date || '').slice(0, 10);
      if (periodRange.start && d && d < periodRange.start) return false;
      if (d && d > periodRange.end) return false;
      if (!variants.length) return true;
      if (serverIds?.has(r.statement_id) || signalLink.ids.has(r.statement_id)) return true;
      const t = r.topic ?? '';
      const hay = [
        r.speaker_name,
        r.person?.name,
        r.speaker_role_at_event,
        r.event?.title,
        headlineQuoteOf(r)?.quote_text,
        r.summary_text,
        r.source?.publisher_title,
        TOPIC_LABELS[t],
        TOPIC_FILTER_LABELS[t],
        topicTitles[t],
      ].join(' ');
      return matchesVariants(hay, variants);
    });
  }, [rows, data, orgs, topics, grades, query, variants, serverHits, signalLink, topicTitles, periodRange]);
  const linkedShown = useMemo(() => filtered.filter(r => signalLink.ids.has(r.statement_id)).length, [filtered, signalLink]);

  const lanes = useMemo(
    () =>
      TOPIC_ORDER.filter(t => topics.has(t)).map(t => {
        const items = filtered.filter(r => r.topic === t);
        return { slug: t, label: TOPIC_LABELS[t], items, eventCount: new Set(items.map(r => r.event?.event_id).filter(Boolean)).size };
      }),
    [topics, filtered],
  );

  // 넓은 화면: 선택이 없거나 필터 밖이면 첫 발언을 선택(스케치처럼 상세가 항상 채워져 있다)
  useEffect(() => {
    if (!isWide || !data) return;
    if (!selectedId || !filtered.some(r => r.statement_id === selectedId)) {
      const first = lanes.find(l => l.items.length > 0)?.items[0];
      setSelectedId(first?.statement_id ?? null);
    }
  }, [isWide, data, lanes, filtered, selectedId]);

  // ?statement= 로 들어온 좁은 화면은 상세 모달을 바로 연다
  useEffect(() => {
    if (!isWide && params.get('statement')) setModalOpen(true);
  }, [isWide, params]);

  const select = (id: string) => {
    setSelectedId(id);
    if (!isWide) setModalOpen(true);
  };
  const closeModal = useCallback(() => setModalOpen(false), []);
  useEffect(() => {
    if (!modalOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && closeModal();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [modalOpen, closeModal]);

  const scope = overview?.scope;
  const queue = overview?.kpi?.people_candidates;

  return (
    <div className="sxbase sx2">
      <header className="top">
        <div className="logo">Policy Signals / Analyst</div>
        <input
          className="search"
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="⌕ 인물, 행사, 발언, 주제 검색"
          aria-label="인물, 행사, 발언, 주제 검색"
        />
        <button
          className="svx-tip"
          aria-disabled="true"
          data-tip={`${PREPARING} — 직책 미확인(후보) 인물 검증 목록은 관리자 > 모니터링 인물 관리에서 확인합니다.`}
          onClick={e => e.preventDefault()}
        >
          검증 큐 {typeof queue === 'number' ? queue : ''}
        </button>
      </header>
      <div className="layout">
        <aside className="filters">
          <div className="group">
            <h3>기관</h3>
            {orgOptions.map(o => (
              <label className="check" key={o.key}>
                {o.label}{' '}
                <span>
                  <input type="checkbox" checked={orgs.has(o.key)} onChange={() => setOrgs(s => toggle(s, o.key))} />{' '}
                  <i className="count">{data ? orgCounts[o.key] ?? 0 : ''}</i>
                </span>
              </label>
            ))}
          </div>
          <div className="group">
            <h3>주제</h3>
            {TOPIC_ORDER.map(t => (
              <label className="check" key={t}>
                {TOPIC_FILTER_LABELS[t]} <input type="checkbox" checked={topics.has(t)} onChange={() => setTopics(s => toggle(s, t))} />
              </label>
            ))}
          </div>
          <div className="group">
            <h3>증거 상태</h3>
            {GRADE_OPTIONS.map(g => (
              <label className="check" key={g.key}>
                {g.label} <input type="checkbox" checked={grades.has(g.key)} onChange={() => setGrades(s => toggle(s, g.key))} />
              </label>
            ))}
          </div>
          <div className="group">
            <h3>기간</h3>
            <div className="svx-period" role="group" aria-label="기간 프리셋">
              {PERIOD_OPTIONS.map(p => (
                <button key={p.key} type="button" aria-pressed={period === p.key} onClick={() => setPeriod(p.key)}>
                  {p.label}
                </button>
              ))}
            </div>
            <div className="check" title={scope?.start ? `수집 범위: ${fmtDot(scope?.start)} — ${fmtDot(scope?.end)}` : undefined}>
              {periodRange.start ? fmtDot(periodRange.start) : scope?.start ? fmtDot(scope.start) : '처음'} — {fmtDot(periodRange.end)}
            </div>
          </div>
        </aside>

        <div className="center">
          <div className="heading">
            <div>
              <h1>주제별 발언 스트림</h1>
              <p>동일 발언은 관련 주제에 교차 표시하며, 행사는 중복 집계하지 않습니다.</p>
            </div>
            <div className="viewtabs">
              <button aria-current="page">스트림</button>
              <button
                aria-disabled={admin ? undefined : 'true'}
                title={admin ? '모니터링 인물 관리' : '관리자 전용'}
                onClick={() => admin && navigate('/admin/stakeholder-people')}
              >
                인물
              </button>
              <button onClick={() => setShowMap(true)}>관계도</button>
            </div>
          </div>

          {!data && !error && <div className="lane-msg">발언을 불러오는 중입니다.</div>}
          {error && (
            <div className="lane-msg err">
              {error}{' '}
              <button type="button" className="svx-linkbtn" onClick={() => setTick(t => t + 1)}>
                다시 시도
              </button>
            </div>
          )}
          {data && rows.length === 0 && <div className="lane-msg">아직 동기화된 발언이 없습니다.</div>}
          {data && variants.length > 0 && signalLink.signalTitles.length > 0 && (
            <p className="svx-search-note">
              정책 시그널 「{signalLink.signalTitles[0]}」{signalLink.signalTitles.length > 1 ? ` 외 ${signalLink.signalTitles.length - 1}건` : ''}의 근거와
              연결된 발언 {linkedShown}건을 함께 표시합니다.
            </p>
          )}
          {data && rows.length > 0 && filtered.length === 0 && (
            <div className="lane-msg">
              필터 조건에 맞는 발언이 없습니다.
              {period !== 'all' && (
                <>
                  {' '}
                  <button type="button" className="svx-linkbtn" onClick={() => setPeriod('all')}>
                    기간을 전체로 넓히기
                  </button>
                </>
              )}
            </div>
          )}

          {lanes
            .filter(l => l.items.length > 0)
            .map(lane => (
              <section className="lane" key={lane.slug}>
                <div className="lane-title">
                  {lane.label}{' '}
                  <span>
                    발언 {lane.items.length} · 행사 {lane.eventCount}
                  </span>
                </div>
                {lane.items.map(r => {
                  const hq = headlineQuoteOf(r);
                  const cls = gradeClassOf(r.evidence_grade, r.grade_class);
                  const summary = firstSentence(r.summary_text);
                  return (
                    <article
                      key={r.statement_id}
                      className={`event${selectedId === r.statement_id ? ' active' : ''}`}
                      tabIndex={0}
                      role="button"
                      aria-label={`${r.speaker_name || '발언자'} 발언 상세 보기`}
                      onClick={() => select(r.statement_id)}
                      onKeyDown={e => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault();
                          select(r.statement_id);
                        }
                      }}
                    >
                      <div className="date">{fmtDot(r.event?.event_date)}</div>
                      <div className="who">
                        <PersonAvatar name={r.person?.name ?? r.speaker_name} personId={r.person?.person_id} hasPhoto={r.person?.photo} className="mini-avatar" />
                        <span>
                          <b>{r.speaker_name || '—'}</b>
                          <small>{r.speaker_role_at_event || '직책 미기재'}</small>
                        </span>
                      </div>
                      <div className="statement">
                        {hq ? (
                          <span className="clamp" data-verbatim="true">
                            {hq.quote_text}
                          </span>
                        ) : summary ? (
                          <span className="clamp">
                            <i className="svx-chip">요약</i>
                            {summary}
                          </span>
                        ) : (
                          '—'
                        )}
                        {hq && isMismatch(hq.locator_match) && (
                          <span className="q-mismatch">
                            <LocatorMatchBadge match="none" />
                          </span>
                        )}
                      </div>
                      <div className={`pill${cls === 'a' ? '' : ` ${cls}`}`} title={gradeTitle(r.evidence_grade)}>
                        {r.evidence_grade ? gradeLabel(r.evidence_grade) : '—'}
                      </div>
                    </article>
                  );
                })}
              </section>
            ))}
        </div>

        <aside className="detail">
          {selectedId ? (
            <Detail statementId={selectedId} copyrightNotice={overview?.copyright_notice} />
          ) : (
            <div className="detail-top">
              <span>발언 상세</span>
              <span>{data ? '발언을 선택하세요' : ''}</span>
            </div>
          )}
        </aside>
      </div>

      {!isWide && modalOpen && selectedId && (
        <div className="sxm-wrap" role="dialog" aria-modal="true" aria-label="발언 상세" onClick={closeModal}>
          <div className="sxm" onClick={e => e.stopPropagation()}>
            <div className="sxm-head">
              <h2>발언 상세</h2>
              <button onClick={closeModal} aria-label="닫기">
                ×
              </button>
            </div>
            <div className="sxm-body">
              <div className="detail in-modal">
                <Detail statementId={selectedId} copyrightNotice={overview?.copyright_notice} />
              </div>
            </div>
          </div>
        </div>
      )}
      {showMap && <RelationshipMapModal onClose={() => setShowMap(false)} />}
    </div>
  );
}
