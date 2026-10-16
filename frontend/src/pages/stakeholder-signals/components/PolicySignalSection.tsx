import { useEffect, useState } from 'react';
import { ApiError } from '@/api/client';
import { fetchSignals, safeHttpUrl, type SVPolicySignal, type SVSignalEvidence, type SVSignalList } from '@/api/stakeholder';
import { IntegrityBadge, LocatorMatchBadge } from './GradeBadge';
import { ExcerptToggle } from './statementDetail';
import { josaIGa, ORG_SHORT } from '../sketch/helpers';

// 스케치 001 section.card.policy-signal (#policySignal) — loadPolicySignal() 이 그리는 마크업 그대로.
//   eyebrow · policy-head(h2 제목 / p thesis / .signal-state "분석 가설 · 공식선언 아님")
//   .confidence(방향성 x/10 · 시행모형 x/10) · .policy-grid(확인된 근거 | 잠재 이점) · .contract · .policy-limit · .policy-links
// 확인된 근거(confirmed_evidence)와 분석 추론(analyst_inference)·미확인 범위(not_confirmed)는 스케치처럼 서로 다른 상자에 둔다
// (소스 README 규칙 6). 신뢰도는 분석가 판단임을 라벨로 밝힌다(보정된 확률 아님).
// 로딩·빈·오류 상태 문구도 스케치 그대로.

const TARGET_SIGNAL = 'SIG-NHIS-FLEX-RSA-COMPOSITE-20261015';
const ROLE_MAP: Record<string, string> = { direct_support: '직접 지지', planned_policy: '추진 정책', adjacent_context: '인접 맥락' };
const STATUS_MAP: Record<string, string> = { analyst_hypothesis: '분석 가설 · 공식선언 아님' };

type State =
  | { kind: 'loading' }
  | { kind: 'ready'; signal: SVPolicySignal; importStatus: SVSignalList['import_status'] }
  | { kind: 'missing' }
  | { kind: 'http'; status: number }
  | { kind: 'shape' };

function score(v?: number | null): string {
  return typeof v === 'number' ? `${(v * 10).toFixed(1)}/10` : '—/10';
}

function shortOrg(s?: string | null): string {
  return (s && ORG_SHORT[s]) || s || '';
}

/** 근거 출처 링크 — 스케치의 3개 링크(인물별 1개, 정책자료)를 confirmed_evidence.source 로 만든다. */
function evidenceLinks(ev: SVSignalEvidence[]): { href: string; label: string }[] {
  const seen = new Set<string>();
  const out: { href: string; label: string }[] = [];
  ev.forEach(e => {
    const href = safeHttpUrl(e.source?.url);
    const who = e.person || '';
    if (!href || seen.has(who) || seen.has(href)) return;
    seen.add(who);
    seen.add(href);
    const org = ORG_SHORT[who];
    out.push({ href, label: org ? `${org} 정책자료` : `${who} 관련 근거` });
  });
  return out;
}

export default function PolicySignalSection({ onEvidenceCount }: { onEvidenceCount?: (n: number) => void }) {
  const [state, setState] = useState<State>({ kind: 'loading' });

  useEffect(() => {
    let cancelled = false;
    fetchSignals()
      .then(d => {
        if (cancelled) return;
        const s = d.signals.find(x => x.signal_id === TARGET_SIGNAL) ?? d.signals[0];
        if (!s) setState(d.import_status?.state === 'invalid' ? { kind: 'shape' } : { kind: 'missing' });
        else if (!s.title || !Array.isArray(s.confirmed_evidence)) setState({ kind: 'shape' });
        else {
          setState({ kind: 'ready', signal: s, importStatus: d.import_status });
          onEvidenceCount?.(s.confirmed_evidence.length);
        }
      })
      .catch(e => {
        if (cancelled) return;
        console.error('policy signal render failed', e);
        setState(e instanceof ApiError ? { kind: 'http', status: e.status } : { kind: 'shape' });
      });
    return () => {
      cancelled = true;
    };
  }, [onEvidenceCount]);

  if (state.kind !== 'ready') {
    const h2 =
      state.kind === 'loading'
        ? '정책 시그널을 불러오는 중입니다'
        : state.kind === 'missing'
          ? '표시할 정책 시그널이 없습니다'
          : '정책 시그널을 표시하지 못했습니다';
    const p =
      state.kind === 'loading'
        ? '확인된 발언과 분석 가설을 분리해 표시합니다.'
        : state.kind === 'http'
          ? '시그널 API 응답을 확인해 주세요.'
          : '정책 시그널 데이터 구조를 확인해 주세요.';
    return (
      <section className="card policy-signal" aria-live="polite">
        <div className="eyebrow">보험자 정책 방향성 예측</div>
        <div className="policy-head">
          <div>
            <h2>{h2}</h2>
            <p>{p}</p>
          </div>
          <span className="signal-state">분석 가설</span>
        </div>
      </section>
    );
  }

  const s = state.signal;
  const ev = s.confirmed_evidence ?? [];
  const inf = s.analyst_inference ?? {};
  const benefits = inf.payer_benefits ?? [];
  const m = s.confidence_methodology;
  const methodTitle = m
    ? [m.scale, m.method, m.limitation, m.reviewed_by ? `검토: ${m.reviewed_by}` : null].filter(Boolean).join('\n')
    : '분석가 판단 점수 — 보정된 확률이 아닙니다.';
  const inst = shortOrg(s.institution) || '보험자';
  const links = evidenceLinks(ev);
  const imp = state.importStatus;

  return (
    <section className="card policy-signal" aria-live="polite">
      <div className="eyebrow">보험자 정책 방향성 예측 · {s.as_of} 기준</div>
      {imp?.state === 'invalid' && (
        <p className="policy-limit import-note" role="status">
          <b>최신 시그널 파일 검증 실패:</b> 마지막 정상본을 표시합니다.{imp.error ? ` (${imp.error})` : ''}
        </p>
      )}
      <div className="policy-head">
        <div>
          <h2>{s.title}</h2>
          <p>{s.thesis}</p>
        </div>
        <span className="signal-state">{STATUS_MAP[s.status ?? ''] ?? s.status ?? '분석 가설'}</span>
      </div>
      <div className="confidence">
        <span title={m?.direction_basis ?? undefined}>방향성 {score(s.direction_confidence)}</span>
        <span title={m?.implementation_basis ?? undefined}>시행모형 {score(s.implementation_model_confidence)}</span>
        <span className="judgement" title={methodTitle}>
          분석가 판단 점수 · 보정된 확률 아님
        </span>
      </div>
      <div className="policy-grid">
        <div className="policy-box">
          <h3>확인된 발언·정책 근거</h3>
          <ul>
            {ev.map((e, i) => (
              <li key={`${e.source_id}-${i}`}>
                <b>{e.person}</b> · {e.date} · {ROLE_MAP[e.evidence_role ?? ''] ?? e.evidence_role} — {e.claim}{' '}
                <small>
                  ({e.evidence_grade}
                  {e.scope_note ? ` · ${e.scope_note}` : ''})
                </small>
                <span className="svb-row ev-marks">
                  <LocatorMatchBadge match={e.locator_match} claim />
                  <IntegrityBadge status={e.integrity_status} />
                  <ExcerptToggle
                    sourceId={e.source_id}
                    locator={e.locator}
                    label="근거 원문 보기"
                    className="svx-linkbtn ev-open"
                  />
                </span>
              </li>
            ))}
          </ul>
        </div>
        <div className="policy-box">
          <h3>
            {inst}
            {josaIGa(inst)} 얻을 수 있는 잠재 이점 <em>· 분석 추론</em>
          </h3>
          <ul>
            {benefits.map((x, i) => (
              <li key={i}>
                {x.benefit} <small>{x.modality} · 근거: {(x.basis_source_ids ?? []).join(', ')}</small>
              </li>
            ))}
          </ul>
        </div>
      </div>
      <div className="contract">
        <b>가장 개연성 높은 구조</b>
        <br />
        {inf.likely_contract_architecture}
        <br />
        <small>우선 적용 예상: {inf.most_likely_first_use}</small>
      </div>
      <p className="policy-limit">
        <b>확인 한계:</b> {s.not_confirmed}
      </p>
      {links.length > 0 && (
        <div className="policy-links">
          {links.map(l => (
            <a key={l.href} href={l.href} target="_blank" rel="noopener noreferrer">
              {l.label}
            </a>
          ))}
        </div>
      )}
    </section>
  );
}
