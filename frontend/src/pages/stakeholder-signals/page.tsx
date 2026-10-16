import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  fetchOverview,
  fetchStatements,
  headlineQuoteOf,
  TOPIC_ORDER,
  type SVOverview,
  type SVStatementRow,
} from '@/api/stakeholder';
import PersonAvatar from './components/PersonAvatar';
import { GradeBadge, IntegrityBadge, LocatorMatchBadge } from './components/GradeBadge';
import { isMismatch } from './components/evidence';
import TimelineDrawer from './components/TimelineDrawer';
import PolicySignalSection from './components/PolicySignalSection';
import { fmtDot, monthDayKo, PREPARING, TOPIC_ROW_LABELS, TOPIC_TAB_LABELS } from './sketch/helpers';
import './sketch/base.css';
import './sketch/common.css';
import './sketch/s001.css';
import './sketch/s001.additions.css';

// Stakeholder Signals — 리더십 타임라인 = 스케치 001(main 영역) 그대로.
//   스케치 자체 좌측 nav 는 제외(대쉬보드 사이드바가 그 역할). 클래스·구조·문구는 스케치와 같고 값만 API 로 채운다.
//   "내보내기"·"+ 행사 업데이트" 는 보류 기능 — 스케치 모양 그대로 두되 비활성(title "준비 중").
// 데이터: overview · statements · statements/<id>(드로어) · signals(정책 시그널)

interface TimelineRow {
  key: string;
  row: SVStatementRow;
  topics: string[];
}

/** 같은 (행사 × 발언자) 블록을 1행으로 — 스케치 행의 data-topic="access finance" 처럼 교차 주제를 한 행에 모은다. */
function groupRows(rows: SVStatementRow[]): TimelineRow[] {
  const out: TimelineRow[] = [];
  const byKey = new Map<string, TimelineRow>();
  rows.forEach(r => {
    const k = `${r.event?.event_id ?? r.statement_id}::${r.speaker_name ?? ''}`;
    const hit = byKey.get(k);
    if (!hit) {
      const g = { key: k, row: r, topics: r.topic ? [r.topic] : [] };
      byKey.set(k, g);
      out.push(g);
      return;
    }
    if (r.topic && !hit.topics.includes(r.topic)) hit.topics.push(r.topic);
    if (!headlineQuoteOf(hit.row) && headlineQuoteOf(r)) hit.row = r;
  });
  return out;
}

function topicText(topics: string[]): string {
  const sorted = [...topics].sort((a, b) => TOPIC_ORDER.indexOf(a as never) - TOPIC_ORDER.indexOf(b as never));
  return sorted.map(t => TOPIC_ROW_LABELS[t] ?? t).join('·') || '—';
}

function daysBetween(a: string, b: string): number {
  return (Date.parse(a.slice(0, 10)) - Date.parse(b.slice(0, 10))) / 86400000;
}

function hostOf(url?: string | null): string {
  try {
    return url ? new URL(url).hostname.replace(/^www\./, '') : '';
  } catch {
    return '';
  }
}

export default function StakeholderSignalsPage() {
  const [overview, setOverview] = useState<SVOverview | null>(null);
  const [ovError, setOvError] = useState(false);
  const [rows, setRows] = useState<SVStatementRow[] | null>(null);
  const [stError, setStError] = useState(false);
  const [evidenceCount, setEvidenceCount] = useState<number | null>(null);
  const [topic, setTopic] = useState('all');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setOvError(false);
    setStError(false);
    fetchOverview()
      .then(d => !cancelled && setOverview(d))
      .catch(() => !cancelled && setOvError(true));
    fetchStatements()
      .then(d => !cancelled && setRows(d))
      .catch(() => !cancelled && setStError(true));
    return () => {
      cancelled = true;
    };
  }, [tick]);

  const all = useMemo(() => rows ?? [], [rows]);
  const grouped = useMemo(() => groupRows(all), [all]);
  const visible = useMemo(() => {
    if (topic === 'all') return grouped;
    // 주제 탭: 그 주제의 발언 블록을 대표로, 행의 주제 칸은 같은 (행사×발언자) 의 교차 주제 전체
    const topicsByKey = new Map(grouped.map(g => [g.key, g.topics]));
    return all
      .filter(r => r.topic === topic)
      .map(r => {
        const k = `${r.event?.event_id ?? r.statement_id}::${r.speaker_name ?? ''}`;
        return { key: r.statement_id, row: r, topics: topicsByKey.get(k) ?? [topic] };
      });
  }, [all, grouped, topic]);

  const closeDrawer = useCallback(() => setSelectedId(null), []);

  const kpi = overview?.kpi;
  const integ = overview?.integrity;
  const asOf = overview?.as_of ?? new Date().toISOString().slice(0, 10);
  const recent30 = useMemo(() => {
    const ids = new Set<string>();
    all.forEach(r => {
      const d = r.event?.event_date;
      if (d && r.event?.event_id && daysBetween(asOf, d) <= 30 && daysBetween(asOf, d) >= 0) ids.add(r.event.event_id);
    });
    return ids.size;
  }, [all, asOf]);

  // 헤드라인 = 가장 최근 행사(발언자·발언 블록·출처는 statements 에서)
  const lead = overview?.recent_events?.[0] ?? null;
  const leadRows = useMemo(() => all.filter(r => r.event?.event_id === lead?.event_id), [all, lead]);
  const leadPeople = useMemo(() => {
    const seen = new Map<string, SVStatementRow>();
    leadRows.forEach(r => {
      if (r.speaker_name && !seen.has(r.speaker_name)) seen.set(r.speaker_name, r);
    });
    return [...seen.values()].slice(0, 3);
  }, [leadRows]);
  const leadSource = leadRows.find(r => r.source)?.source ?? null;
  const leadIsThisMonth = lead?.event_date ? daysBetween(asOf, lead.event_date) <= 31 : false;

  const v = (n?: number | null) => (typeof n === 'number' ? n : '—');
  const preparing = (e: React.MouseEvent) => e.preventDefault();

  return (
    <div className="sxbase sx1">
      <div className="main">
        <div className="top">
          <div className="crumb">약가·건강보험 재정 / Intelligence Dashboard</div>
          <div className="actions">
            <button className="btn" aria-disabled="true" title={PREPARING} onClick={preparing}>
              내보내기
            </button>
            <button className="btn dark" aria-disabled="true" title={PREPARING} onClick={preparing}>
              + 행사 업데이트
            </button>
          </div>
        </div>

        <section className="hero">
          <div>
            <h1>정책 발언 인텔리전스</h1>
            <p>사람의 말에서 제도 변화의 방향과 실행 시그널을 읽습니다.</p>
          </div>
          <div className="stamp">
            마지막 검증 {integ?.last_sync_at ? fmtDot(integ.last_sync_at) : '—'} KST
            <br />
            {evidenceCount !== null
              ? `정책 시그널 근거 ${evidenceCount}건 교차검토`
              : `원문 무결성 ${integ?.sources_verified ?? 0} / ${integ?.sources_total ?? 0} 확인`}
          </div>
        </section>

        <section className="metrics">
          <div className="card metric">
            <div className="label">포착 행사</div>
            <strong>{v(kpi?.events)}</strong>
            <div className="delta">
              {ovError ? (
                <>
                  요약 지표를 불러오지 못했습니다 ·{' '}
                  <button type="button" className="svx-linkbtn" onClick={() => setTick(t => t + 1)}>
                    다시 시도
                  </button>
                </>
              ) : (
                <>
                  <b>최근 30일 {rows ? recent30 : '—'}건</b> · 중복 제거
                </>
              )}
            </div>
          </div>
          <div className="card metric">
            <div className="label">확인 발언</div>
            <strong>{v(kpi?.quotes)}</strong>
            <div className="delta" title="출처 원문 파일 SHA-256 대조(출처 기준)">
              원문 대조{' '}
              <b>
                {integ?.sources_verified ?? '—'} / {integ?.sources_total ?? '—'}
              </b>
            </div>
          </div>
          <div className="card metric">
            <div className="label">핵심 인물</div>
            <strong>{v(kpi?.people_active)}</strong>
            <div className="delta">우선 모니터링 {kpi?.people_priority ?? '—'}명</div>
          </div>
          <div className="card metric">
            <div className="label">후속 확인 필요</div>
            <strong>{v(kpi?.people_candidates)}</strong>
            <div className="delta">공식 직책 미확인 후보</div>
          </div>
        </section>

        <section className="signal">
          <div className="card headline">
            <div className="headline-copy">
              <div className="eyebrow">{leadIsThisMonth || !lead ? '이번 달 핵심 시그널' : '최근 핵심 시그널'}</div>
              <h2>{lead?.title || (overview ? '최근 행사가 없습니다' : '불러오는 중…')}</h2>
              {lead && (
                <p>
                  {(lead.speakers ?? []).join(', ')} — <b>발언 블록 {leadRows.length}건</b>
                  {(lead.topics ?? []).length > 0 && <>. 주제: {(lead.topics ?? []).join(' · ')}</>}.
                </p>
              )}
            </div>
            {lead && leadPeople.length > 0 && (
              <figure className="signal-photo">
                <div className="photo-stack" aria-hidden="true">
                  {leadPeople.map(r => (
                    <PersonAvatar
                      key={r.statement_id}
                      name={r.person?.name ?? r.speaker_name}
                      personId={r.person?.person_id}
                      hasPhoto={r.person?.photo}
                      className="avatar"
                    />
                  ))}
                </div>
                <figcaption>
                  {leadPeople.map(r => r.speaker_name).join('·')}
                  <br />
                  {[hostOf(leadSource?.url), fmtDot(leadSource?.publication_date)].filter(x => x && x !== '—').join(' · ')}
                </figcaption>
              </figure>
            )}
          </div>
          <div className="card mini-list">
            {(overview?.recent_events ?? []).slice(0, 3).map(ev => (
              <div className="mini" key={ev.event_id}>
                <span>{monthDayKo(ev.event_date)}</span>
                <b>{ev.title}</b>
                <br />
                <small>{(ev.speakers ?? []).join(' · ')}</small>
              </div>
            ))}
            {overview && (overview.recent_events ?? []).length === 0 && <div className="mini">최근 행사가 없습니다.</div>}
          </div>
        </section>

        <PolicySignalSection onEvidenceCount={setEvidenceCount} />

        <div className="tabs" role="tablist" aria-label="주제">
          {['all', ...TOPIC_ORDER].map(t => (
            <button
              key={t}
              role="tab"
              aria-selected={topic === t}
              className={`tab${topic === t ? ' active' : ''}`}
              onClick={() => setTopic(t)}
            >
              {t === 'all' ? '전체' : TOPIC_TAB_LABELS[t]}
            </button>
          ))}
        </div>
        <div className="legend" aria-label="증거등급 범례">
          <b>증거등급</b>
          <span>
            <i className="dot a"></i>A 공식 원문
          </span>
          <span>
            <i className="dot b"></i>B 기관 자료·인용
          </span>
          <span>
            <i className="dot c"></i>C 전문언론 인용·요약
          </span>
        </div>

        <section className="table">
          <div className="thead">
            <div>행사일</div>
            <div>주제</div>
            <div>행사</div>
            <div>인물·직책</div>
            <div>확인된 발언</div>
            <div>증거</div>
          </div>
          {!rows && !stError && <div className="row row-msg">발언을 불러오는 중입니다.</div>}
          {stError && (
            <div className="row row-msg err">
              발언 목록을 불러오지 못했습니다 ·{' '}
              <button type="button" className="svx-linkbtn" onClick={() => setTick(t => t + 1)}>
                다시 시도
              </button>
            </div>
          )}
          {rows && visible.length === 0 && (
            <div className="row row-msg">{all.length === 0 ? '아직 동기화된 발언이 없습니다.' : '이 주제에 해당하는 발언이 없습니다.'}</div>
          )}
          {visible.map(({ key, row, topics }) => {
            const hq = headlineQuoteOf(row);
            const open = () => setSelectedId(row.statement_id);
            return (
              <div
                key={key}
                className="row"
                tabIndex={0}
                role="button"
                aria-label={`${row.speaker_name || '발언자'} 발언 상세 보기`}
                onClick={open}
                onKeyDown={e => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    open();
                  }
                }}
              >
                <div className="date">{fmtDot(row.event?.event_date)}</div>
                <div className="org">{topicText(topics)}</div>
                <div>{row.event?.title || '—'}</div>
                <div className="person">
                  <PersonAvatar name={row.person?.name ?? row.speaker_name} personId={row.person?.person_id} hasPhoto={row.person?.photo} />
                  <span>
                    <b>{row.speaker_name || '—'}</b>
                    <small>{row.speaker_role_at_event || '직책 미기재'}</small>
                  </span>
                </div>
                <div className="quote">
                  {hq ? (
                    <span data-verbatim="true">{hq.quote_text}</span>
                  ) : row.summary_text ? (
                    <>
                      <i className="svx-chip">요약</i>
                      {row.summary_text}
                    </>
                  ) : (
                    '—'
                  )}
                  {hq && isMismatch(hq.locator_match) && (
                    <span className="q-mismatch">
                      <LocatorMatchBadge match="none" />
                    </span>
                  )}
                </div>
                <div className="evidence-cell">
                  <span className="ev-stack">
                    <GradeBadge grade={row.evidence_grade} gradeClass={row.grade_class} />
                    {row.source && <IntegrityBadge status={row.source.integrity_status} />}
                  </span>
                  <span className="row-more">›</span>
                </div>
              </div>
            );
          })}
        </section>
      </div>

      <TimelineDrawer statementId={selectedId} copyrightNotice={overview?.copyright_notice} onClose={closeDrawer} />
    </div>
  );
}
