import { Fragment, useEffect, useMemo, useState } from 'react';
import {
  fetchAnalogFacets, searchAnalog, fetchAnalogDetail, generateAnalogBrief, submitSearchFeedback,
  FACET_LABELS, REVIEW_RESULT_KO,
  type AnalogFacets, type AnalogReport, type AnalogSearchResult, type AnalogBrief, type EfficacyEndpoint,
} from '@/api/analog';

const FACET_KEYS = [
  'disease_category', 'disease_category_detail', 'cancer_type', 'line_of_therapy',
  'review_result', 'reimbursement_track_ko', 'coverage_gap_type', 'approval_driver',
];

const GAP_STYLE: Record<string, string> = {
  '축소': 'bg-orange-50 text-orange-600 border-orange-200',
  '확대': 'bg-emerald-50 text-emerald-600 border-emerald-200',
  '구체화': 'bg-sky-50 text-sky-600 border-sky-200',
  '동일': 'bg-gray-100 text-gray-500 border-gray-200',
  '비교불가': 'bg-gray-50 text-gray-400 border-gray-200',
};

const RESULT_COLOR: Record<string, string> = {
  APPROVED: 'text-emerald-600 bg-emerald-50',
  CONDITIONAL_APPROVED: 'text-amber-600 bg-amber-50',
  APPROVED_WITH_POSTMARKET: 'text-blue-600 bg-blue-50',
  REJECTED: 'text-red-500 bg-red-50',
  UNKNOWN: 'text-gray-400 bg-gray-50',
};

// ── 효과 지표 표 ────────────────────────────────────────────────────────────

function EfficacyTable({ data }: { data: EfficacyEndpoint[] }) {
  if (!data.length) return null;
  return (
    <div className="overflow-x-auto rounded-lg border border-gray-200">
      <table className="w-full text-xs">
        <thead>
          <tr className="bg-gray-50 border-b border-gray-200 text-gray-500">
            <th className="px-2 py-1.5 text-left">임상시험</th>
            <th className="px-2 py-1.5 text-left">지표</th>
            <th className="px-2 py-1.5 text-right">신청품</th>
            <th className="px-2 py-1.5 text-right">비교군</th>
            <th className="px-2 py-1.5 text-right">HR</th>
            <th className="px-2 py-1.5 text-right">p값</th>
          </tr>
        </thead>
        <tbody>
          {data.map((ep, i) => (
            <tr key={i} className="border-b border-gray-100 last:border-0">
              <td className="px-2 py-1.5 text-gray-600 font-mono whitespace-nowrap">{ep.trial_name ?? '—'}</td>
              <td className="px-2 py-1.5">
                <span className="font-bold text-teal-700">{ep.endpoint}</span>
                {ep.endpoint_ko && <span className="text-gray-400 ml-1">({ep.endpoint_ko})</span>}
                {ep.endpoint_detail && <div className="text-gray-400">{ep.endpoint_detail}</div>}
              </td>
              <td className="px-2 py-1.5 text-right font-bold tabular-nums">
                {ep.value != null ? `${ep.value}${ep.value_unit ?? ''}` : '—'}
              </td>
              <td className="px-2 py-1.5 text-right tabular-nums text-gray-500">
                {ep.comparator_value != null ? `${ep.comparator_value}${ep.value_unit ?? ''}` : '—'}
                {ep.comparator_name && <div className="text-gray-400">{ep.comparator_name}</div>}
              </td>
              <td className="px-2 py-1.5 text-right tabular-nums text-violet-600">
                {ep.hr != null ? ep.hr.toFixed(2) : '—'}
              </td>
              <td className="px-2 py-1.5 text-right tabular-nums text-gray-500">
                {ep.p_value ?? '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── 타임라인 ──────────────────────────────────────────────────────────────────

const _normDate = (s: string | null | undefined): string | null =>
  s ? s.trim().replace(/[./]/g, '-') : null;

function safeParse(s: string | null | undefined): Record<string, unknown> | null {
  if (!s) return null;
  try { const v = JSON.parse(s); return v && typeof v === 'object' ? v as Record<string, unknown> : null; } catch { return null; }
}
function parseJsonArr(s: string | null | undefined): unknown[] {
  if (!s) return [];
  try { const v = JSON.parse(s); return Array.isArray(v) ? v : []; } catch { return []; }
}

function Timeline({ r }: { r: AnalogReport }) {
  const events: { date: string; label: string; type: string }[] = [];
  const permit = _normDate(r.mfds_permit_date);
  if (permit) events.push({ date: permit, label: '식약처 허가', type: 'mfds' });
  // 사전심의 위원회 — 암질환심의위원회(암/중증) 또는 약제급여기준소위원회(일반약제)
  {
    const amj = r.amjilsim_history ?? [];
    // 위원회 유형별 차수 카운트 (같은 위원회 2회 이상이면 N차 표기)
    const byType: Record<string, number> = {};
    amj.forEach(e => { const c = e.committee || '암질환심의위원회'; byType[c] = (byType[c] ?? 0) + 1; });
    const seen: Record<string, number> = {};
    amj.forEach(e => {
      const d = _normDate(e.date);
      const c = e.committee || '암질환심의위원회';
      seen[c] = (seen[c] ?? 0) + 1;
      const ord = byType[c] > 1 ? ` ${seen[c]}차` : '';
      const type = c === '약제급여기준소위원회' ? 'subcommittee' : 'amjilsim';
      if (d) events.push({ date: d, label: `${c}${ord} (급여기준 설정)`, type });
    });
  }
  (r.committee_history ?? []).forEach(e => {
    const d = _normDate(e.date);
    const res = REVIEW_RESULT_KO[e.result ?? ''] ?? e.result ?? '';
    if (d) events.push({
      date: d,
      label: `약제급여평가위원회${e.ordinal ? ` ${e.ordinal}차` : ''}${res ? ` (${res})` : ''}`,
      type: 'committee',
    });
  });
  const reimb = _normDate(r.first_reimbursement_date);
  if (reimb) events.push({ date: reimb, label: '급여 등재 (최초 약가)', type: 'reimbursement' });
  events.sort((a, b) => a.date.localeCompare(b.date));
  if (events.length === 0) return null;

  const typeStyle: Record<string, { dot: string; chip: string }> = {
    mfds: { dot: 'bg-blue-500', chip: 'bg-blue-50 text-blue-700 border-blue-200' },
    amjilsim: { dot: 'bg-orange-500', chip: 'bg-orange-50 text-orange-700 border-orange-200' },
    subcommittee: { dot: 'bg-indigo-500', chip: 'bg-indigo-50 text-indigo-700 border-indigo-200' },
    committee: { dot: 'bg-teal-500', chip: 'bg-teal-50 text-teal-700 border-teal-200' },
    reimbursement: { dot: 'bg-emerald-500', chip: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
  };

  // 강조 구간: 최초 허가 → 급여 등재 (없으면 허가 → 약평위 통과 fallback)
  const passEvent = [...events].reverse().find(e => e.type === 'committee');
  let total: number | null = null;
  let lagLabel = '최초 허가 → 급여 등재';
  if (permit && reimb) {
    const d = (Date.parse(reimb) - Date.parse(permit)) / 86400000;
    if (Number.isFinite(d) && d >= 0) total = Math.round(d);
  }
  if (total == null) {  // 급여등재일 없으면 기존 허가→약평위
    lagLabel = '최초 허가 → 약평위';
    total = r.lag_days_approval_to_reimb;
    if (total == null && permit && passEvent) {
      const d = (Date.parse(passEvent.date) - Date.parse(permit)) / 86400000;
      if (Number.isFinite(d) && d >= 0) total = Math.round(d);
    }
  }
  const lagAnchor = reimb || passEvent?.date;

  return (
    <div className="space-y-3">
      {/* 전체 소요일 요약 배너 */}
      {total != null && permit && lagAnchor && (
        <div className="flex items-center gap-3 rounded-lg bg-gradient-to-r from-blue-50 to-emerald-50 border border-emerald-200 px-3 py-2">
          <i className="ri-time-line text-emerald-600"></i>
          <div className="text-xs">
            <span className="text-gray-500">{lagLabel}</span>
            <span className="font-bold text-emerald-700 mx-1.5 text-sm tabular-nums">
              {total.toLocaleString()}일
            </span>
            <span className="text-gray-400">({(total / 365).toFixed(1)}년)</span>
          </div>
          {(r.requeue_count ?? 0) > 0 && (
            <span className="ml-auto text-[11px] text-amber-600 bg-amber-50 border border-amber-200 rounded px-1.5 py-0.5">
              재심의 {r.requeue_count}회
            </span>
          )}
        </div>
      )}

      {/* 이벤트 타임라인 (세로 라인) */}
      <div className="relative pl-3">
        <div className="absolute left-[5px] top-1 bottom-1 w-px bg-gray-200" />
        <div className="space-y-2">
          {events.map((ev, i) => {
            const st = typeStyle[ev.type] ?? { dot: 'bg-gray-400', chip: 'bg-gray-100 text-gray-600 border-gray-200' };
            return (
              <div key={i} className="relative flex items-center gap-2.5 text-xs">
                <span className={`absolute -left-3 w-2.5 h-2.5 rounded-full ring-2 ring-white ${st.dot}`} />
                <span className="text-gray-400 tabular-nums w-24 shrink-0 pl-1">{ev.date}</span>
                <span className={`px-1.5 py-0.5 rounded border text-[11px] font-semibold ${st.chip}`}>
                  {ev.label}
                </span>
              </div>
            );
          })}
        </div>
      </div>
      {!permit && (
        <p className="text-[11px] text-gray-400">※ 식약처 허가일 미수집 — 소요일 계산 불가</p>
      )}
    </div>
  );
}

// ── 상세 모달 ────────────────────────────────────────────────────────────────

function DetailModal({ r, onClose }: { r: AnalogReport; onClose: () => void }) {
  const efficacy = r.efficacy_data ?? [];
  const comparators = r.comparator_drugs ?? [];
  const trials = r.clinical_trials ?? [];
  const policyTags = r.policy_tags ?? [];
  const hasTimeline = (r.committee_history?.length ?? 0) > 0 || r.mfds_permit_date;
  // 위험분담제(RSA) 조건 표시: rsa_types 우선 → rsa_type_hint → has_rsa
  const rsaLabel = (r.rsa_types && r.rsa_types.length > 0)
    ? r.rsa_types.join(', ')
    : (r.has_rsa ? (r.rsa_type_hint || '적용') : null);
  // 깨진 줄바꿈 이어붙이기 (PDF 추출 시 문장 중간 개행 제거)
  const joinLines = (s: string | null | undefined): string =>
    s ? s.replace(/\s*\n\s*/g, ' ').replace(/[ \t]{2,}/g, ' ').trim() : '';

  // 원문 줄바꿈 정리: 문단/항목 구조는 보존하되 PDF 줄바꿈으로 끊긴
  // 문장 중간 개행만 공백으로 이어붙임. (마커: ○ ▢ · 가. 1) ① (1) 등으로 시작
  // 하거나, 직전 줄이 문장부호로 끝나면 새 줄 유지)
  const MARKER_RE = /^\s*(?:[○▢◦•·*]|[-–—]\s|[가-힣]\s*[.)]|\d+\s*[.)]|[①-⑳]|\([가-힣0-9]+\)|[<【\[])/;
  const ENDER_RE = /[.。!?:][")'\]」』]?\s*$/;
  const tidyText = (s: string | null | undefined): string => {
    if (!s) return '';
    const lines = s.replace(/\r/g, '').split('\n').map(l => l.trim()).filter(Boolean);
    const out: string[] = [];
    for (const l of lines) {
      const prev = out[out.length - 1];
      if (prev && !MARKER_RE.test(l) && !ENDER_RE.test(prev)) {
        out[out.length - 1] = `${prev} ${l}`;
      } else {
        out.push(l);
      }
    }
    return out.join('\n').replace(/[ \t]{2,}/g, ' ');
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="bg-white rounded-2xl border border-gray-200 w-full max-w-4xl max-h-[92vh] overflow-y-auto shadow-xl">
        {/* 헤더 */}
        <div className="sticky top-0 bg-white border-b border-gray-100 px-6 py-4 flex items-start justify-between z-10">
          <div>
            <h2 className="text-xl font-bold flex items-center gap-2 flex-wrap">
              {r.brand_name ?? '(약제명 미상)'}
              {r.dosage && (
                <span className="align-middle text-sm font-semibold text-gray-500 bg-gray-100 border border-gray-200 rounded px-2 py-0.5">
                  {r.dosage}
                </span>
              )}
              {r.post_url && (
                <a href={r.post_url} target="_blank" rel="noopener noreferrer"
                  title="약평위 게시물 열기 (HIRA)"
                  className="inline-flex items-center gap-1 text-xs font-semibold text-teal-600 hover:text-teal-700 border border-teal-200 bg-teal-50 rounded-md px-2 py-0.5">
                  <i className="ri-external-link-line"></i>약평위 게시물
                </a>
              )}
            </h2>
            <p className="text-sm text-gray-500">
              {r.generic_name_en && <span className="italic mr-2">{r.generic_name_en}</span>}
              {r.manufacturer && <span className="mr-2">{r.manufacturer}</span>}
              {r.session_date} 약평위 {r.ordinal}차
            </p>
          </div>
          <button onClick={onClose} className="w-9 h-9 flex items-center justify-center rounded-lg hover:bg-gray-100 text-gray-400">
            <i className="ri-close-line text-lg"></i>
          </button>
        </div>

        <div className="px-6 py-5 space-y-5">
          {/* 핵심 지표 그리드 */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
            {[
              ['심의결과', REVIEW_RESULT_KO[r.review_result ?? ''] ?? r.review_result, RESULT_COLOR[r.review_result ?? ''] ?? ''],
              ['등재트랙', r.reimbursement_track_ko || r.reimbursement_track, ''],
              ['허가↔급여 갭', r.coverage_gap_type, GAP_STYLE[r.coverage_gap_type ?? ''] ?? ''],
              ['위험분담제(RSA)', rsaLabel, rsaLabel ? 'text-rose-600' : ''],
            ].map(([l, v, cls]) => (
              <div key={String(l)} className="bg-gray-50 border border-gray-100 rounded-lg p-2.5">
                <p className="text-[10px] text-gray-400">{l}</p>
                <p className={`font-bold ${cls || 'text-gray-900'}`}>{v || '—'}</p>
              </div>
            ))}
          </div>

          {/* 질환 분류 · 정책의도 (좌) ｜ 등재 타임라인 (우) */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 items-start">
            {/* 좌측: 질환 분류 + HIRA 정책 의도 (세로 스택) */}
            <div className="space-y-3">
              {/* 질환 정보 */}
              <div className="rounded-lg border border-gray-200 p-3 space-y-1 text-xs">
                <p className="text-[11px] font-bold text-gray-500 mb-1.5">질환 분류</p>
                <div className="flex flex-wrap gap-1.5">
              {r.disease_category && (
                <span className="px-2 py-0.5 rounded-full bg-teal-50 text-teal-700 border border-teal-200 font-semibold">
                  {r.disease_category}
                </span>
              )}
              {r.disease_category_detail && (
                <span className="px-2 py-0.5 rounded-full bg-teal-50 text-teal-600 border border-teal-200">
                  {r.disease_category_detail}
                </span>
              )}
              {r.cancer_type && (
                <span className="px-2 py-0.5 rounded-full bg-violet-50 text-violet-700 border border-violet-200 font-mono">
                  {r.cancer_type}
                </span>
              )}
              {r.line_of_therapy && (
                <span className="px-2 py-0.5 rounded-full bg-sky-50 text-sky-700 border border-sky-200">
                  {r.line_of_therapy}
                </span>
              )}
              {r.biomarker && (
                <span className="px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200">
                  {r.biomarker}
                </span>
              )}
              {r.treatment_setting && (
                <span className="px-2 py-0.5 rounded-full bg-gray-100 text-gray-600 border border-gray-200">
                  {r.treatment_setting}
                </span>
              )}
            </div>
                {(r.disease_name_ko || r.disease_name) && (
                  <p className="text-gray-600 mt-1">{r.disease_name_ko || r.disease_name}</p>
                )}
              </div>

              {/* HIRA 정책 의도 (질환 분류 바로 아래) */}
              {(r.policy_intent_summary || policyTags.length > 0) && (
                <div className="rounded-lg border border-teal-200 bg-teal-50/50 p-3 space-y-2">
                  <p className="text-[11px] font-bold text-teal-700">HIRA 정책 의도</p>
                  {r.policy_intent_summary && (
                    <p className="text-xs text-gray-700 leading-relaxed">{r.policy_intent_summary}</p>
                  )}
                  {r.approval_driver && (
                    <span className="inline-block px-2 py-0.5 rounded bg-teal-100 text-teal-800 text-[11px] font-bold">
                      {r.approval_driver}
                    </span>
                  )}
                  {policyTags.length > 0 && (
                    <div className="flex flex-wrap gap-1 pt-0.5">
                      {policyTags.map(t => (
                        <span key={t} className="px-1.5 py-0.5 bg-white border border-teal-200 text-teal-600 text-[11px] rounded">
                          {t}
                        </span>
                      ))}
                    </div>
                  )}
                  {r.future_conditions && (
                    <p className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1">
                      향후 조건: {r.future_conditions}
                    </p>
                  )}
                </div>
              )}
            </div>

            {/* 우측: 등재 타임라인 (질환 분류 옆) */}
            <div className="rounded-lg border border-gray-200 p-3">
              <p className="text-[11px] font-bold text-gray-500 mb-1.5">등재 타임라인</p>
              {hasTimeline
                ? <Timeline r={r} />
                : <p className="text-[11px] text-gray-400">타임라인 정보 미수집</p>}
            </div>
          </div>

          {/* 허가 ↔ 급여 적응증 */}
          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-lg border border-gray-200 p-3">
              <p className="text-[11px] font-bold text-gray-500 mb-1">
                식약처 허가 적응증
                {r.mfds_permit_date && <span className="ml-1 font-normal text-gray-400">({r.mfds_permit_date})</span>}
              </p>
              <p className="text-xs text-gray-700 max-h-48 overflow-y-auto leading-relaxed">
                {joinLines(r.mfds_effect_text) || '허가 정보 미수집'}
              </p>
            </div>
            <div className="rounded-lg border border-gray-200 p-3">
              <p className="text-[11px] font-bold text-gray-500 mb-1">급여 승인 적응증</p>
              <p className="text-xs text-gray-700">{r.disease_name_ko || r.disease_name || '—'}</p>
              {r.coverage_gap_type && (
                <div className={`mt-2 rounded p-2 border ${GAP_STYLE[r.coverage_gap_type] ?? 'bg-gray-50 border-gray-200'}`}>
                  <p className="font-bold text-[11px]">갭: {r.coverage_gap_type}</p>
                  <p className="text-[11px] mt-0.5 leading-relaxed">{r.coverage_gap_evidence}</p>
                </div>
              )}
            </div>
          </div>

          {/* 위험분담제(RSA) 미디어 보완 조건 — Tier1 전문지(급여등재 ±2개월), PDF 원본과 분리 */}
          {(() => {
            const conds = parseJsonArr(r.rsa_media_conditions);
            const sources = parseJsonArr(r.rsa_media_sources);
            const mon = r.rsa_media_monitoring ? safeParse(r.rsa_media_monitoring) : null;
            const monMetrics = Array.isArray(mon?.metrics) ? mon.metrics : [];
            if (!conds.length && !sources.length) return null;
            return (
              <div className="rounded-lg border border-violet-200 bg-violet-50/40 p-3">
                <p className="text-[11px] font-bold text-violet-700 mb-1.5 flex items-center gap-1.5">
                  <i className="ri-newspaper-line"></i>RSA·사후조건 미디어 보완 (급여 등재 시점 Tier1 전문지)
                  {r.rsa_media_confidence && <span className="text-[10px] font-normal text-violet-400">신뢰도 {r.rsa_media_confidence}</span>}
                </p>
                {conds.length > 0 && (
                  <ul className="text-xs text-gray-700 space-y-0.5 list-disc pl-4">
                    {conds.map((c, i) => <li key={i}>{String(c)}</li>)}
                  </ul>
                )}
                {mon && (mon.duration_months || monMetrics.length || mon.review) && (
                  <p className="text-[11px] text-gray-500 mt-1.5">
                    사후 모니터링: {mon.duration_months ? `${mon.duration_months}개월 ` : ''}
                    {monMetrics.length ? `· 지표 ${monMetrics.join(', ')} ` : ''}
                    {mon.review ? `· ${mon.review}` : ''}
                  </p>
                )}
                {sources.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mt-2">
                    {sources.map((s, i) => (
                      <a key={i} href={(s as { url?: string }).url} target="_blank" rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-[10px] text-violet-600 hover:text-violet-800 border border-violet-200 bg-white rounded px-1.5 py-0.5">
                        <i className="ri-external-link-line"></i>{(s as { media?: string }).media || '출처'}{(s as { date?: string }).date ? ` ${(s as { date?: string }).date}` : ''}
                      </a>
                    ))}
                  </div>
                )}
              </div>
            );
          })()}

          {/* 효과 보완 카드 — 대체약제 / 외국 등재국가수 / 의견조회 학회 */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {/* 대체약제 */}
            <div className="rounded-lg border border-orange-200 bg-orange-50/40 p-3">
              <p className="text-[11px] font-bold text-orange-700 mb-1.5">
                <i className="ri-capsule-line mr-1"></i>대체약제 (비교약제)
              </p>
              {comparators.length > 0 ? (
                <div className="flex flex-wrap gap-1">
                  {comparators.map(c => (
                    <span key={c} className="px-2 py-0.5 bg-white text-orange-700 border border-orange-200 rounded-full text-[11px]">
                      {c}
                    </span>
                  ))}
                </div>
              ) : <p className="text-[11px] text-gray-400">미수집</p>}
            </div>
            {/* 외국 등재국가수 (A8/A7) */}
            <div className="rounded-lg border border-sky-200 bg-sky-50/40 p-3">
              <p className="text-[11px] font-bold text-sky-700 mb-1.5">
                <i className="ri-global-line mr-1"></i>A{r.foreign_listing_basis ?? 8} 등재국가수
              </p>
              {r.foreign_listing_count != null ? (
                <p className="text-sm">
                  <span className="font-bold text-sky-700 text-lg tabular-nums">{r.foreign_listing_count}</span>
                  <span className="text-gray-500 text-xs"> / A{r.foreign_listing_basis ?? 8} {r.foreign_listing_basis ?? 8}개국</span>
                </p>
              ) : <p className="text-[11px] text-gray-400">미수집</p>}
            </div>
            {/* 의견조회 학회 */}
            <div className="rounded-lg border border-violet-200 bg-violet-50/40 p-3">
              <p className="text-[11px] font-bold text-violet-700 mb-1.5">
                <i className="ri-team-line mr-1"></i>의견조회 학회
              </p>
              {(r.consulted_societies?.length ?? 0) > 0 ? (
                <div className="flex flex-wrap gap-1">
                  {r.consulted_societies.map(s => (
                    <span key={s} className="px-2 py-0.5 bg-white text-violet-700 border border-violet-200 rounded-full text-[11px]">
                      {s}
                    </span>
                  ))}
                </div>
              ) : <p className="text-[11px] text-gray-400">미수집</p>}
            </div>
          </div>

          {/* 효과 지표 */}
          {(efficacy.length > 0 || r.os_months || r.pfs_months) && (
            <div>
              <p className="text-[11px] font-bold text-gray-500 mb-1.5">임상 효과 지표</p>
              {/* 요약 수치 */}
              {(r.os_months || r.pfs_months || r.orr_pct || r.key_hr) && (
                <div className="flex gap-2 mb-2 flex-wrap">
                  {r.os_months && (
                    <div className="bg-emerald-50 border border-emerald-200 rounded-lg px-3 py-1.5 text-center">
                      <p className="text-[10px] text-emerald-600">OS</p>
                      <p className="text-sm font-bold text-emerald-700">{r.os_months}개월</p>
                    </div>
                  )}
                  {r.pfs_months && (
                    <div className="bg-sky-50 border border-sky-200 rounded-lg px-3 py-1.5 text-center">
                      <p className="text-[10px] text-sky-600">PFS</p>
                      <p className="text-sm font-bold text-sky-700">{r.pfs_months}개월</p>
                    </div>
                  )}
                  {r.orr_pct && (
                    <div className="bg-violet-50 border border-violet-200 rounded-lg px-3 py-1.5 text-center">
                      <p className="text-[10px] text-violet-600">ORR</p>
                      <p className="text-sm font-bold text-violet-700">{r.orr_pct}%</p>
                    </div>
                  )}
                  {r.key_hr && (
                    <div className="bg-amber-50 border border-amber-200 rounded-lg px-3 py-1.5 text-center">
                      <p className="text-[10px] text-amber-600">HR</p>
                      <p className="text-sm font-bold text-amber-700">{r.key_hr.toFixed(2)}</p>
                    </div>
                  )}
                </div>
              )}
              <EfficacyTable data={efficacy} />
            </div>
          )}

          {/* 임상시험명 */}
          {trials.length > 0 && (
            <div className="text-xs">
              <p className="text-[11px] font-bold text-gray-500 mb-1">임상시험</p>
              <div className="flex flex-wrap gap-1">
                {trials.map(t => (
                  <span key={t} className="px-2 py-0.5 bg-gray-100 text-gray-600 border border-gray-200 rounded-full font-mono">
                    {t}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* 평가 결과 원문 */}
          {r.decision_reason && (
            <details className="rounded-lg border border-gray-200">
              <summary className="px-3 py-2 text-xs font-bold text-gray-500 cursor-pointer select-none hover:bg-gray-50">
                가. 평가 결과 원문 (펼치기)
              </summary>
              <div className="px-3 pb-3">
                <p className="text-xs text-gray-600 whitespace-pre-line leading-relaxed max-h-56 overflow-y-auto">
                  {tidyText(r.decision_reason)}
                </p>
              </div>
            </details>
          )}

          {/* 평가 내용 원문 */}
          {r.body_text && (
            <details className="rounded-lg border border-gray-200">
              <summary className="px-3 py-2 text-xs font-bold text-gray-500 cursor-pointer select-none hover:bg-gray-50">
                나. 평가 내용 원문 (펼치기)
              </summary>
              <div className="px-3 pb-3">
                <p className="text-xs text-gray-600 whitespace-pre-line leading-relaxed max-h-64 overflow-y-auto">
                  {tidyText(r.body_text)}
                </p>
              </div>
            </details>
          )}
        </div>
      </div>
    </div>
  );
}

// ── 메인 페이지 ───────────────────────────────────────────────────────────────

export default function AnalogSearchPage() {
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [query, setQuery] = useState('');
  const [data, setData] = useState<AnalogSearchResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<AnalogReport | null>(null);
  const [brief, setBrief] = useState<AnalogBrief | null>(null);
  const [briefLoading, setBriefLoading] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const [showFeedback, setShowFeedback] = useState(false);
  const [feedbackSent, setFeedbackSent] = useState(false);

  // 캐스케이드 패싯: 앞단 선택이 바뀔 때마다 뒷단 옵션·카운트를 재계산
  const [facetData, setFacetData] = useState<AnalogFacets | null>(null);
  useEffect(() => {
    let alive = true;
    fetchAnalogFacets(filters)
      .then(d => { if (alive) setFacetData(d); })
      .catch(e => { console.error(e); });
    return () => { alive = false; };
  }, [filters]);

  const runSearch = async () => {
    setLoading(true); setBrief(null);
    try {
      const res = await searchAnalog({
        filters, q: query.trim() || undefined, limit: 60,
      });
      setData(res);
    } catch (e) { console.error(e); setData(null); }
    finally { setLoading(false); }
  };

  useEffect(() => { runSearch(); /* eslint-disable-next-line */ }, []);

  // 캐스케이드: 한 단계를 바꾸면 그 뒤(하위) 단계 선택은 모두 해제 — 무효 조합 방지
  const setFacet = (k: string, v: string) =>
    setFilters(prev => {
      const n = { ...prev };
      if (v) n[k] = v; else delete n[k];
      const idx = FACET_KEYS.indexOf(k);
      if (idx >= 0) FACET_KEYS.slice(idx + 1).forEach(dk => delete n[dk]);
      return n;
    });

  const results = data?.results ?? [];
  const briefIds = useMemo(() => results.slice(0, 10).map(r => r.id), [results]);

  const makeBrief = async () => {
    if (!results.length) return;
    setBriefLoading(true);
    try {
      const ctx = [query, ...Object.values(filters)].filter(Boolean).join(' · ');
      setBrief(await generateAnalogBrief(briefIds, ctx));
    } catch (e) { setBrief({ brief: '', cited_ids: [], error: String(e) }); }
    finally { setBriefLoading(false); }
  };

  const openDetail = async (id: number) => {
    try { setSelected(await fetchAnalogDetail(id)); } catch (e) { console.error(e); }
  };

  const sendFeedback = async (intended: string, note: string) => {
    const top = results.slice(0, 5).map(r => r.brand_name ?? r.brand_name_raw ?? '').filter(Boolean).join(', ');
    await submitSearchFeedback({
      query: query.trim(),
      filters,
      returned_ids: results.slice(0, 10).map(r => r.id),
      returned_top: top,
      intended_text: intended,
      note,
    });
    setFeedbackSent(true);
  };

  return (
    <div className="min-h-screen bg-gray-50 text-gray-900">
      <div className="px-8 pt-8 pb-5 border-b border-gray-200 bg-white">
        <div className="flex items-center gap-2 mb-1">
          <i className="ri-search-eye-line text-teal-600 text-xl"></i>
          <h1 className="text-2xl font-bold">등재 아날로그 검색</h1>
        </div>
        <p className="text-gray-500 text-sm">
          약평위 평가 607개 사례 (DREC Raw PDF) — 질환분류·OS/PFS·대체약제·등재국가수·타임라인·정책의도 포함
        </p>
      </div>

      <div className="px-8 py-6 space-y-5">
        {/* 검색 컨트롤 */}
        <div className="rounded-2xl border border-gray-200 bg-white p-5 space-y-4">
          <div className="grid grid-cols-4 lg:grid-cols-8 gap-2">
            {FACET_KEYS.map(k => (
              <div key={k}>
                <label className="text-[10px] font-semibold text-gray-400 uppercase">
                  {FACET_LABELS[k] ?? k}
                </label>
                <select value={filters[k] ?? ''} onChange={e => setFacet(k, e.target.value)}
                  className="w-full mt-0.5 text-xs border border-gray-200 rounded-lg px-2 py-2 bg-gray-50 focus:outline-none focus:border-teal-300">
                  <option value="">전체</option>
                  {(facetData?.[k] ?? []).map(o => (
                    <option key={o.value} value={o.value}>
                      {REVIEW_RESULT_KO[o.value] ?? o.value} ({o.count})
                    </option>
                  ))}
                </select>
              </div>
            ))}
          </div>
          <div className="flex gap-3">
            <div className="flex-1">
              <div className="flex items-center gap-1.5">
                <label className="text-[10px] font-semibold text-gray-400 uppercase">통합 검색</label>
                <button type="button" onClick={() => setShowHelp(true)}
                  title="검색 방법 안내"
                  className="text-gray-400 hover:text-teal-600 transition-colors leading-none">
                  <i className="ri-question-line text-sm"></i>
                </button>
              </div>
              <div className="relative mt-0.5">
                <i className="ri-search-line absolute left-3 top-1/2 -translate-y-1/2 text-gray-400"></i>
                <input value={query} onChange={e => setQuery(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && runSearch()}
                  placeholder="약제명·성분·질환·기전·제형 검색"
                  className="w-full text-sm border border-gray-200 rounded-lg pl-9 pr-3 py-2 bg-gray-50 focus:outline-none focus:border-teal-300" />
              </div>
            </div>
            <div className="flex items-end gap-2">
              <button onClick={runSearch}
                className="bg-teal-600 text-white text-sm font-bold px-5 py-2 rounded-lg hover:bg-teal-700 transition-colors whitespace-nowrap">
                <i className="ri-search-line mr-1"></i>검색
              </button>
              <button onClick={() => { setFilters({}); setQuery(''); }}
                className="text-gray-500 text-xs px-3 py-2 rounded-lg hover:bg-gray-100">초기화</button>
            </div>
          </div>
        </div>

        {/* 동의어/조건 인식 칩 — 쿼리가 매핑된 concept(teal) + 도메인 필드(indigo) */}
        {data?.query_debug && data.query_debug.matched_concepts.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <span className="text-gray-400"><i className="ri-sparkling-line mr-0.5"></i>인식된 조건:</span>
            {data.query_debug.matched_concepts.map((c, i) => {
              const isField = c.type === 'field';
              return (
                <Fragment key={c.concept_id}>
                  {data.query_debug?.and_rerank && i > 0 && (
                    <span className="text-gray-400 font-semibold">AND</span>
                  )}
                  <span
                    className={
                      'px-2 py-0.5 rounded-full border font-semibold ' +
                      (isField
                        ? 'bg-indigo-50 text-indigo-700 border-indigo-200'
                        : 'bg-teal-50 text-teal-700 border-teal-200')
                    }
                    title={c.canonical_en ?? ''}>
                    {c.canonical_ko ?? c.concept_id}
                  </span>
                </Fragment>
              );
            })}
            {data.query_debug.and_rerank ? (
              <span className="ml-1 text-[11px] text-indigo-500">· 모든 조건 일치 우선 정렬 (AND)</span>
            ) : data.query_debug.tag_rerank && (
              <span className="ml-1 text-[11px] text-gray-400">· 의미 일치 우선 정렬</span>
            )}
          </div>
        )}

        {/* 결과 헤더 */}
        <div className="flex items-center justify-between">
          <p className="text-sm text-gray-500">
            {loading ? '검색 중…' : `${results.length}건`}
            {data?.mode === 'search' && <span className="ml-2 text-teal-600 text-xs">관련도 정렬 (약제명·성분 우선)</span>}
          </p>
          <div className="flex items-center gap-2">
            {/* 검색 결과가 의도와 다를 때 — 실제 찾던 약제를 남겨 검색 로직 개선에 반영 */}
            {data && (query.trim() || Object.keys(filters).length > 0) && (
              <button onClick={() => { setFeedbackSent(false); setShowFeedback(true); }}
                className="flex items-center gap-1.5 bg-white border border-amber-300 text-amber-700 text-xs font-bold px-4 py-2 rounded-lg hover:bg-amber-50 transition-colors">
                <i className="ri-feedback-line"></i>
                원하는 결과가 아닌가요?
              </button>
            )}
            {results.length > 0 && (
              <button onClick={makeBrief} disabled={briefLoading}
                className="flex items-center gap-1.5 bg-white border border-teal-300 text-teal-700 text-xs font-bold px-4 py-2 rounded-lg hover:bg-teal-50 transition-colors disabled:opacity-60">
                <i className={briefLoading ? 'ri-loader-4-line animate-spin' : 'ri-lightbulb-flash-line'}></i>
                {briefLoading ? '브리프 생성 중…' : '전략 브리프 (상위 10건)'}
              </button>
            )}
          </div>
        </div>

        {/* 브리프 */}
        {brief && (
          <div className="rounded-2xl border border-teal-200 bg-teal-50/60 p-5">
            {brief.error ? (
              <p className="text-sm text-red-500">브리프 실패: {brief.error}</p>
            ) : (
              <>
                <div className="flex items-center gap-2 mb-2">
                  <i className="ri-lightbulb-flash-line text-teal-600"></i>
                  <h3 className="font-bold text-sm text-teal-700">아날로그 전략 브리프</h3>
                  {brief.cached && <span className="text-[10px] text-gray-400">캐시</span>}
                </div>
                <div className="text-sm text-gray-700 whitespace-pre-line leading-relaxed">{brief.brief}</div>
                <p className="text-[10px] text-gray-400 mt-2">근거 사례 {brief.cited_ids.length}건</p>
              </>
            )}
          </div>
        )}

        {/* 결과 표 */}
        <div className="rounded-2xl border border-gray-200 bg-white overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="bg-gray-50 border-b border-gray-200 text-left text-gray-500">
                  <th className="px-3 py-2.5 font-semibold w-8">#</th>
                  <th className="px-3 py-2.5 font-semibold">약제</th>
                  <th className="px-3 py-2.5 font-semibold whitespace-nowrap">용량</th>
                  <th className="px-3 py-2.5 font-semibold">질환 / 치료차수</th>
                  <th className="px-3 py-2.5 font-semibold">효과지표</th>
                  <th className="px-3 py-2.5 font-semibold whitespace-nowrap">결과</th>
                  <th className="px-3 py-2.5 font-semibold">트랙</th>
                  <th className="px-3 py-2.5 font-semibold">갭</th>
                  <th className="px-3 py-2.5 font-semibold whitespace-nowrap">날짜·차수</th>
                </tr>
              </thead>
              <tbody>
                {results.map((r, i) => (
                  <tr key={r.id} onClick={() => openDetail(r.id)}
                    className="border-b border-gray-100 last:border-0 hover:bg-teal-50/40 cursor-pointer">
                    <td className="px-3 py-2.5 text-gray-400 tabular-nums">{i + 1}</td>
                    <td className="px-3 py-2.5 max-w-[180px]">
                      <div className="font-bold text-gray-900 truncate" title={r.brand_name ?? ''}>{r.brand_name ?? '(약제명 미상)'}</div>
                      <div className="text-gray-400 truncate">
                        {r.generic_name_en || r.generic_name || ''}
                        {r.similarity != null && <span className="ml-1 text-teal-500">·{r.similarity.toFixed(2)}</span>}
                      </div>
                    </td>
                    <td className="px-3 py-2.5 text-gray-500 whitespace-nowrap tabular-nums">
                      {r.dosage || '—'}
                    </td>
                    <td className="px-3 py-2.5 max-w-[200px]">
                      <div className="truncate text-gray-700" title={r.disease_name_ko || r.disease_name || ''}>
                        {r.disease_name_ko || r.disease_name || '—'}
                      </div>
                      <div className="text-gray-400">
                        {r.cancer_type && <span className="mr-1">{r.cancer_type}</span>}
                        {r.line_of_therapy && <span>{r.line_of_therapy}</span>}
                      </div>
                    </td>
                    <td className="px-3 py-2.5 tabular-nums text-gray-600">
                      {r.os_months && <div className="text-emerald-600">OS {r.os_months}mo</div>}
                      {r.pfs_months && <div className="text-sky-600">PFS {r.pfs_months}mo</div>}
                      {!r.os_months && !r.pfs_months && r.orr_pct && <div>ORR {r.orr_pct}%</div>}
                      {!r.os_months && !r.pfs_months && !r.orr_pct && <span className="text-gray-300">—</span>}
                    </td>
                    <td className="px-3 py-2.5 whitespace-nowrap">
                      <span className={`inline-block px-1.5 py-0.5 rounded font-semibold ${RESULT_COLOR[r.review_result ?? ''] ?? 'text-gray-400'}`}>
                        {REVIEW_RESULT_KO[r.review_result ?? ''] ?? r.review_result ?? '—'}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-gray-600 max-w-[140px]">
                      <div className="truncate" title={r.reimbursement_track_ko || r.reimbursement_track || ''}>
                        {r.reimbursement_track_ko || r.reimbursement_track || '—'}
                      </div>
                      {r.has_rsa === 1 && <span className="text-violet-500">위험분담</span>}
                    </td>
                    <td className="px-3 py-2.5">
                      {r.coverage_gap_type ? (
                        <span className={`px-1.5 py-0.5 rounded-full border font-semibold ${GAP_STYLE[r.coverage_gap_type] ?? 'bg-gray-50 text-gray-400 border-gray-200'}`}>
                          {r.coverage_gap_type}
                        </span>
                      ) : <span className="text-gray-300">—</span>}
                    </td>
                    <td className="px-3 py-2.5 text-gray-400 tabular-nums whitespace-nowrap">
                      {r.session_date}{r.ordinal ? ` ·${r.ordinal}차` : ''}
                    </td>
                  </tr>
                ))}
                {!loading && results.length === 0 && (
                  <tr>
                    <td colSpan={9} className="px-3 py-10 text-center text-gray-400">
                      조건에 맞는 사례 없음
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {selected && <DetailModal r={selected} onClose={() => setSelected(null)} />}
      {showHelp && <SearchHelpModal onClose={() => setShowHelp(false)} />}
      {showFeedback && (
        <SearchFeedbackModal
          query={query.trim()}
          resultCount={results.length}
          sent={feedbackSent}
          onSubmit={sendFeedback}
          onClose={() => setShowFeedback(false)}
        />
      )}
    </div>
  );
}

// ── 검색어 피드백 모달 ───────────────────────────────────────────────────────────

function SearchFeedbackModal({
  query, resultCount, sent, onSubmit, onClose,
}: {
  query: string;
  resultCount: number;
  sent: boolean;
  onSubmit: (intended: string, note: string) => Promise<void>;
  onClose: () => void;
}) {
  const [intended, setIntended] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const submit = async () => {
    if (!intended.trim()) { setErr('실제 찾고자 했던 약제/내용을 입력해 주세요.'); return; }
    setBusy(true); setErr('');
    try { await onSubmit(intended.trim(), note.trim()); }
    catch (e) { setErr('저장 실패: ' + String(e)); }
    finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="bg-white rounded-2xl max-w-md w-full shadow-xl" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100">
          <div className="flex items-center gap-2">
            <i className="ri-feedback-line text-amber-600 text-lg"></i>
            <h3 className="font-bold text-gray-900">검색어 피드백</h3>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-700">
            <i className="ri-close-line text-xl"></i>
          </button>
        </div>

        {sent ? (
          <div className="px-6 py-8 text-center space-y-3">
            <i className="ri-checkbox-circle-line text-emerald-500 text-4xl"></i>
            <p className="text-sm text-gray-700">피드백이 저장됐습니다. 검색 로직 개선에 반영하겠습니다.</p>
            <button onClick={onClose}
              className="mt-1 bg-gray-100 text-gray-600 text-sm font-medium px-5 py-2 rounded-lg hover:bg-gray-200">
              닫기
            </button>
          </div>
        ) : (
          <div className="px-6 py-5 space-y-4 text-sm">
            <p className="text-gray-500 leading-relaxed">
              입력하신 검색어로 원하는 결과가 나오지 않았다면, <span className="font-medium text-gray-700">실제 찾고자 했던 약제</span>를 알려주세요.
              검색어 의미를 파악해 로직을 개선하는 데 사용됩니다.
            </p>

            <div className="rounded-lg bg-gray-50 border border-gray-100 px-3 py-2 text-xs text-gray-500">
              입력한 검색어: <span className="font-medium text-gray-700">{query || '(없음)'}</span>
              <span className="mx-1.5">·</span>결과 {resultCount}건
            </div>

            <div>
              <label className="text-xs font-semibold text-gray-600">실제 찾던 약제 / 내용 <span className="text-red-400">*</span></label>
              <input value={intended} onChange={e => setIntended(e.target.value)}
                autoFocus
                placeholder="예: 키트루다, PD-1 면역항암제 1차, 린파자 난소암"
                className="w-full mt-1 text-sm border border-gray-200 rounded-lg px-3 py-2 bg-gray-50 focus:outline-none focus:border-amber-300" />
            </div>

            <div>
              <label className="text-xs font-semibold text-gray-600">추가 설명 (선택)</label>
              <textarea value={note} onChange={e => setNote(e.target.value)}
                rows={3}
                placeholder="어떤 결과를 기대했는지, 무엇이 달랐는지 자유롭게 적어주세요."
                className="w-full mt-1 text-sm border border-gray-200 rounded-lg px-3 py-2 bg-gray-50 focus:outline-none focus:border-amber-300 resize-none" />
            </div>

            {err && <p className="text-xs text-red-500">{err}</p>}

            <div className="flex justify-end gap-2 pt-1">
              <button onClick={onClose}
                className="text-gray-500 text-sm px-4 py-2 rounded-lg hover:bg-gray-100">취소</button>
              <button onClick={submit} disabled={busy}
                className="flex items-center gap-1.5 bg-amber-600 text-white text-sm font-bold px-5 py-2 rounded-lg hover:bg-amber-700 transition-colors disabled:opacity-60">
                <i className={busy ? 'ri-loader-4-line animate-spin' : 'ri-send-plane-line'}></i>
                {busy ? '저장 중…' : '피드백 보내기'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── 검색 방법 안내 모달 ─────────────────────────────────────────────────────────

function SearchHelpModal({ onClose }: { onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}>
      <div className="bg-white rounded-2xl max-w-lg w-full max-h-[85vh] overflow-y-auto shadow-xl"
        onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100 sticky top-0 bg-white">
          <div className="flex items-center gap-2">
            <i className="ri-search-eye-line text-teal-600 text-lg"></i>
            <h3 className="font-bold text-gray-900">검색 방법 안내</h3>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-700">
            <i className="ri-close-line text-xl"></i>
          </button>
        </div>

        <div className="px-6 py-5 space-y-5 text-sm text-gray-700">
          <div>
            <div className="flex items-center gap-1.5 font-semibold text-gray-900 mb-1.5">
              <i className="ri-funnel-line text-teal-600"></i>상단 드롭다운 — 단계별 좁히기
            </div>
            <p className="text-gray-600 leading-relaxed">
              <span className="font-medium">질환군 → 세부질환군 → 암종 → 치료차수 → 심의결과 → 등재트랙</span> 순서로
              앞단을 고르면 뒷단 선택지가 그 조합에 맞게 자동으로 좁혀집니다.
              각 선택지 뒤 숫자는 현재 조합에서의 사례 수이며 앞단 선택에 따라 함께 바뀝니다.
              앞단을 바꾸면 뒷단 선택은 자동 해제됩니다.
            </p>
          </div>

          <div>
            <div className="flex items-center gap-1.5 font-semibold text-gray-900 mb-1.5">
              <i className="ri-sparkling-line text-teal-600"></i>동의어 자동 인식
            </div>
            <p className="text-gray-600 leading-relaxed">
              같은 개념의 다른 표현을 자동으로 묶어 검색합니다. 예: <span className="font-medium">고지혈증 주사제 = 이상지질혈증 주사제 = PCSK9 주사제</span> 는 같은 결과를 보여줍니다.
              성분명·기전·타깃·제형까지 연결됩니다.
            </p>
          </div>

          <div>
            <div className="flex items-center gap-1.5 font-semibold text-gray-900 mb-1.5">
              <i className="ri-add-circle-line text-teal-600"></i>띄어쓰기 = AND 검색
            </div>
            <p className="text-gray-600 leading-relaxed">
              단어 사이를 띄우면 <span className="font-medium">모든 조건을 동시에 만족</span>하는 사례가 상단에 정렬됩니다.
            </p>
            <ul className="mt-1.5 space-y-1 text-gray-600">
              <li><span className="px-1.5 py-0.5 rounded bg-gray-100 text-xs font-medium">희귀 항암제</span> → 희귀의약품 + 항암제 동시 태그</li>
              <li><span className="px-1.5 py-0.5 rounded bg-gray-100 text-xs font-medium">난소암 항암제</span> → 난소암 + 항암제 동시 태그</li>
              <li><span className="px-1.5 py-0.5 rounded bg-gray-100 text-xs font-medium">경평면제 총액제한</span> → 경제성평가 생략 + 총액제한 RSA</li>
            </ul>
          </div>

          <div>
            <div className="flex items-center gap-1.5 font-semibold text-gray-900 mb-1.5">
              <i className="ri-price-tag-3-line text-teal-600"></i>인식된 조건 칩
            </div>
            <p className="text-gray-600 leading-relaxed">
              검색 후 쿼리가 어떻게 해석됐는지 칩으로 표시됩니다.
              <span className="px-1.5 py-0.5 mx-0.5 rounded-full border bg-teal-50 text-teal-700 border-teal-200 text-xs font-semibold">개념</span>(동의어 매칭) /
              <span className="px-1.5 py-0.5 mx-0.5 rounded-full border bg-indigo-50 text-indigo-700 border-indigo-200 text-xs font-semibold">필드</span>(구조화 필드) 로 구분되며,
              AND 검색이면 조건 사이에 AND 가 붙습니다.
            </p>
          </div>

          <div>
            <div className="flex items-center gap-1.5 font-semibold text-gray-900 mb-1.5">
              <i className="ri-medicine-bottle-line text-teal-600"></i>약제명 정확 일치 우선
            </div>
            <p className="text-gray-600 leading-relaxed">
              약제명(예: 키트루다)을 입력하면 해당 약제가 최상단에 노출됩니다.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
