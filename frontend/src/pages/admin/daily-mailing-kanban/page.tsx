import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { fetchMe } from '@/utils/authUsers';
import {
  fetchDailyMailingKanban, parseJsonArray, asStringArray, normalizeReviewerFindings,
  type DailyMailingKanban, type KanbanArticle, type KanbanRun,
  type Persona, type ReviewerRole, type DraftItem, type QualityReport, type RunCounts,
} from '@/api/dailyMailingKanban';

const LANE_ICON: Record<string, string> = {
  'Dashboard Scope': 'ri-crosshair-2-line',
  'Source Intake': 'ri-inbox-archive-line',
  'Triage/Verify': 'ri-shield-check-line',
  'Writer Agent': 'ri-quill-pen-line',
  'Review Board': 'ri-team-line',
  'Delivery/History': 'ri-send-plane-2-line',
};

// 기사 카드 레인 (그 외 레인은 run-level 정보 카드로 렌더)
const ARTICLE_LANES = new Set(['Source Intake', 'Triage/Verify', 'Review Board', 'Writer Agent']);

const Dash = () => <span className="text-[#4A5568]">—</span>;

function badgeTone(value: string | null | undefined): string {
  if (!value) return 'bg-[#1E2530] text-[#8B9BB4]';
  const v = value.toLowerCase();
  if (['verified', 'official_verified', 'publisher_verified', 'high', 'included', 'tier1', 'tier_1', 'ready_for_writer', 'pass', 'sent'].some(k => v.includes(k))) {
    return 'bg-[#00E5CC]/10 text-[#00E5CC]';
  }
  if (['excluded', 'rejected', 'low', 'fail', 'block'].some(k => v.includes(k))) {
    return 'bg-red-500/10 text-red-400';
  }
  if (['pending', 'unverified', 'medium', 'tier2', 'tier_2', 'needs_review', 'warn', 'gated'].some(k => v.includes(k))) {
    return 'bg-[#F59E0B]/10 text-[#F59E0B]';
  }
  return 'bg-[#3B82F6]/10 text-[#60A5FA]';
}

function DELIVERY_TONE(status: string): string {
  const v = (status || '').toLowerCase();
  if (v.includes('sent') || v.includes('delivered')) return 'bg-[#00E5CC]/10 text-[#00E5CC]';
  if (v.includes('fail') || v.includes('error')) return 'bg-red-500/10 text-red-400';
  if (v.includes('draft')) return 'bg-[#F59E0B]/10 text-[#F59E0B]';
  return 'bg-[#3B82F6]/10 text-[#60A5FA]';
}

// High=적색/앰버, Medium=청색, Watch=회색
function priorityTone(priority: string | null | undefined): string {
  const v = (priority || '').toLowerCase();
  if (v === 'high') return 'bg-red-500/10 text-red-400 border border-red-500/30';
  if (v === 'medium') return 'bg-[#3B82F6]/10 text-[#60A5FA] border border-[#3B82F6]/30';
  if (v === 'watch') return 'bg-[#1E2530] text-[#8B9BB4] border border-[#2A3441]';
  return 'bg-[#1E2530] text-[#8B9BB4]';
}

function decisionTone(decision: string | null | undefined): string {
  const v = (decision || '').toLowerCase();
  if (v === 'pass' || v === 'ok') return 'bg-[#00E5CC]/10 text-[#00E5CC]';
  if (v === 'warn' || v === 'caution') return 'bg-[#F59E0B]/10 text-[#F59E0B]';
  if (v === 'fix' || v === 'fail' || v === 'block') return 'bg-red-500/10 text-red-400';
  return 'bg-[#1E2530] text-[#8B9BB4]';
}

function boolBadge(label: string, value: boolean | null | undefined): JSX.Element {
  const on = value === true;
  return (
    <span className={`text-[10px] px-2 py-0.5 rounded ${on ? 'bg-[#00E5CC]/10 text-[#00E5CC]' : 'bg-[#1E2530] text-[#8B9BB4]'}`}>
      <i className={`${on ? 'ri-checkbox-circle-line' : 'ri-close-circle-line'} mr-0.5`}></i>
      {label}: {on ? 'Yes' : 'No'}
    </span>
  );
}

function Chip({ text, tone = 'bg-[#1E2530] text-[#8B9BB4]' }: { text: string; tone?: string }) {
  return <span className={`text-[10px] px-1.5 py-0.5 rounded ${tone}`}>{text}</span>;
}

function ArticleCard({ article }: { article: KanbanArticle }) {
  const qualityFlags = asStringArray(article.quality_flags);
  const matchedKeywords = asStringArray(article.matched_keywords);
  const trackerTags = asStringArray(article.tracker_tags);
  const findings = normalizeReviewerFindings(article.reviewer_findings);
  const passCount = findings.filter(f => (f?.decision || '').toLowerCase() === 'pass').length;
  return (
    <div className="bg-[#0D1117] border border-[#1E2530] rounded-xl p-3 space-y-2">
      <p className="text-white text-xs font-semibold leading-snug line-clamp-3">{article.title}</p>
      <div className="flex flex-wrap items-center gap-1.5">
        {article.source_name && <Chip text={article.source_name} />}
        {article.source_tier && <Chip text={article.source_tier} tone={badgeTone(article.source_tier)} />}
        {article.source_status && <Chip text={article.source_status} tone={badgeTone(article.source_status)} />}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {article.priority && (
          <span className={`text-[10px] px-2 py-0.5 rounded ${priorityTone(article.priority)}`}>{article.priority}</span>
        )}
        {article.review_status && (
          <Chip text={article.review_status} tone={badgeTone(article.review_status)} />
        )}
        {article.ma_relevance != null && (
          <Chip text={`MA 연관도 ${article.ma_relevance}`} tone="bg-[#7C3AED]/10 text-[#A78BFA]" />
        )}
        {article.score != null && Number(article.score) > 0 && (
          <Chip text={`score ${Number(article.score).toFixed(2)}`} tone="bg-[#1E2530] text-[#8B9BB4]" />
        )}
        {!!article.selected_for_draft && (
          <Chip text="초안 선택됨" tone="bg-[#00E5CC]/10 text-[#00E5CC]" />
        )}
      </div>
      {(article.tracking_lane || trackerTags.length > 0) && (
        <div className="flex flex-wrap items-center gap-1.5">
          {article.tracking_lane && (
            <Chip text={article.tracking_lane} tone="bg-[#3B82F6]/10 text-[#60A5FA]" />
          )}
          {trackerTags.map(t => <Chip key={t} text={t} tone="bg-[#1E2530] text-[#4A5568]" />)}
        </div>
      )}
      {qualityFlags.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          {qualityFlags.map(f => (
            <span key={f} className="text-[10px] px-2 py-0.5 rounded bg-red-500/10 text-red-400">
              <i className="ri-flag-2-line mr-0.5"></i>{f}
            </span>
          ))}
        </div>
      )}
      {matchedKeywords.length > 0 && (
        <div className="flex flex-wrap items-center gap-1">
          {matchedKeywords.slice(0, 5).map(k => <Chip key={k} text={k} tone="bg-[#1E2530] text-[#4A5568]" />)}
        </div>
      )}
      {article.verification_caveat && (
        <p className="text-[10px] text-[#F59E0B] leading-snug">
          <i className="ri-error-warning-line mr-1"></i>{article.verification_caveat}
        </p>
      )}
      {article.verification_method && (
        <p className="text-[10px] text-[#4A5568] leading-snug">
          <i className="ri-fingerprint-line mr-1"></i>검증 방식: {article.verification_method}
        </p>
      )}
      {article.next_action && (
        <p className="text-[10px] text-[#60A5FA] leading-snug">
          <i className="ri-arrow-right-circle-line mr-1"></i>{article.next_action}
        </p>
      )}
      {findings.length > 0 && (
        <details className="group">
          <summary className="cursor-pointer text-[10px] text-[#8B9BB4] hover:text-white list-none flex items-center gap-1">
            <i className="ri-team-line"></i>
            리뷰 소견 {passCount}/{findings.length} pass
            <i className="ri-arrow-down-s-line group-open:rotate-180 transition-transform"></i>
          </summary>
          <div className="mt-1.5 space-y-1.5 border-l-2 border-[#1E2530] pl-2">
            {findings.map((f, i) => (
              <div key={`${f.reviewer ?? i}`} className="space-y-0.5">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-[10px] text-[#8B9BB4] font-semibold">{f.label || f.reviewer || 'reviewer'}</span>
                  <span className={`text-[9px] px-1.5 py-0.5 rounded ${decisionTone(f.decision)}`}>{f.decision || '—'}</span>
                </div>
                {f.rationale && <p className="text-[10px] text-[#4A5568] leading-snug">{f.rationale}</p>}
                {f.required_fix && (
                  <p className="text-[10px] text-[#F59E0B] leading-snug"><i className="ri-tools-line mr-0.5"></i>{f.required_fix}</p>
                )}
              </div>
            ))}
          </div>
        </details>
      )}
      <div className="flex items-center gap-3 pt-1 border-t border-[#1E2530]">
        {article.publisher_url && (
          <a href={article.publisher_url} target="_blank" rel="noreferrer" className="text-[10px] text-[#00E5CC] hover:underline">
            <i className="ri-external-link-line mr-0.5"></i>원문
          </a>
        )}
        {article.naver_url && (
          <a href={article.naver_url} target="_blank" rel="noreferrer" className="text-[10px] text-[#00E5CC] hover:underline">
            <i className="ri-external-link-line mr-0.5"></i>네이버
          </a>
        )}
        {article.official_url && (
          <a href={article.official_url} target="_blank" rel="noreferrer" className="text-[10px] text-[#00E5CC] hover:underline">
            <i className="ri-government-line mr-0.5"></i>공식
          </a>
        )}
        {article.published_at && (
          <span className="text-[10px] text-[#4A5568] ml-auto whitespace-nowrap">{article.published_at}</span>
        )}
      </div>
    </div>
  );
}

// Dashboard Scope / Delivery/History 레인 — 백엔드가 넣는 run-level 항목을 느슨하게 렌더
function InfoCard({ item }: { item: Record<string, unknown> }) {
  const title = (item.title as string) || (item.name as string) || (item.run_id as string) || '항목';
  const statusish: Array<[string, unknown]> = ['status', 'delivery_status', 'approval_status', 'window_label']
    .map(k => [k, item[k]] as [string, unknown])
    .filter(([, v]) => typeof v === 'string' && v);
  const keywords = asStringArray(item.keywords ?? item.matched_keywords);
  const htmlPath = typeof item.html_path === 'string' ? item.html_path : null;
  const ownerEmail = typeof item.owner_email === 'string' ? item.owner_email : null;
  const generatedAt = typeof item.generated_at === 'string' ? item.generated_at : null;
  return (
    <div className="bg-[#0D1117] border border-[#1E2530] rounded-xl p-3 space-y-2">
      <p className="text-white text-xs font-semibold leading-snug line-clamp-2">{String(title)}</p>
      {statusish.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          {statusish.map(([k, v]) => <Chip key={k} text={String(v)} tone={badgeTone(String(v))} />)}
        </div>
      )}
      {keywords.length > 0 && (
        <div className="flex flex-wrap items-center gap-1">
          {keywords.slice(0, 6).map(k => <Chip key={k} text={k} tone="bg-[#1E2530] text-[#4A5568]" />)}
        </div>
      )}
      {ownerEmail && <p className="text-[10px] text-[#8B9BB4]"><i className="ri-mail-line mr-1"></i>{ownerEmail}</p>}
      {htmlPath && (
        <p className="text-[10px] text-[#4A5568]"><i className="ri-file-text-line mr-1"></i>{htmlPath.split('/').pop()}</p>
      )}
      {generatedAt && <p className="text-[10px] text-[#4A5568]">{generatedAt}</p>}
    </div>
  );
}

function QualityReportPanel({ run }: { run: KanbanRun }) {
  const qr: QualityReport = run.quality_report ?? {};
  const counts: RunCounts = run.counts ?? {};
  const warnings = asStringArray(qr.warnings);
  const blocking = asStringArray(qr.blocking_reasons);
  const countCells: Array<[string, number | null | undefined]> = [
    ['발굴', counts.discovered ?? run.discovered_count],
    ['최신', counts.recent ?? run.recent_count],
    ['선택', counts.selected ?? run.selected_count],
    ['needs_review', counts.needs_review],
    ['ready_for_writer', counts.ready_for_writer],
  ];
  return (
    <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-6">
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <h2 className="text-white font-bold text-base">Quality Report</h2>
        <span className="text-[10px] text-[#4A5568] font-mono">{run.run_id}</span>
        {qr.status && (
          <span className={`text-[10px] px-2 py-0.5 rounded ${badgeTone(qr.status)}`}>{qr.status}</span>
        )}
        {boolBadge('sendable', qr.sendable)}
        {boolBadge('live_send_allowed', qr.live_send_allowed)}
      </div>
      <div className="flex flex-wrap gap-4 mb-3">
        {countCells.map(([label, value]) => (
          <div key={label}>
            <p className="text-[#4A5568] text-[10px]">{label}</p>
            <p className="text-white text-sm font-semibold">{value ?? '—'}</p>
          </div>
        ))}
        <div>
          <p className="text-[#4A5568] text-[10px]">top signal / watchlist</p>
          <p className="text-white text-sm font-semibold">{qr.top_signal_count ?? '—'} / {qr.watchlist_count ?? '—'}</p>
        </div>
      </div>
      {blocking.length > 0 && (
        <div className="space-y-1 mb-2">
          {blocking.map(b => (
            <p key={b} className="text-xs text-red-400"><i className="ri-close-circle-line mr-1"></i>{b}</p>
          ))}
        </div>
      )}
      {warnings.length > 0 && (
        <div className="space-y-1 mb-2">
          {warnings.map(w => (
            <p key={w} className="text-xs text-[#F59E0B]"><i className="ri-alert-line mr-1"></i>{w}</p>
          ))}
        </div>
      )}
      <p className="text-[10px] text-[#4A5568]">
        최소 기준: 기사 {qr.min_total_articles ?? '—'}건 이상 · 상위 시그널 {qr.min_top_signals ?? '—'}건 이상
      </p>
    </div>
  );
}

function DraftBriefPanel({ run }: { run: KanbanRun }) {
  const items: DraftItem[] = Array.isArray(run.draft_items) ? run.draft_items : [];
  if (items.length === 0) return null;
  return (
    <div className="bg-[#161B27] rounded-2xl border border-[#00E5CC]/20 p-6">
      <div className="flex items-center gap-2 mb-1">
        <span className="w-5 h-5 flex items-center justify-center"><i className="ri-newspaper-line text-[#00E5CC]"></i></span>
        <h2 className="text-white font-bold text-base">오늘의 브리프</h2>
        <span className="text-[10px] px-2 py-0.5 rounded bg-[#00E5CC]/10 text-[#00E5CC]">{items.length}건 · 헤르메스 초안</span>
      </div>
      <p className="text-[#8B9BB4] text-xs mb-4">최신 run 에서 선택·작성된 브리프 항목 (draft_items)</p>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {items.map((item, idx) => {
          const quotes = asStringArray(item.evidence_quotes);
          return (
            <div key={`${item.title ?? idx}`} className="bg-[#0D1117] border border-[#1E2530] rounded-xl p-4 space-y-2">
              <p className="text-white text-sm font-semibold leading-snug">{item.title || '(제목 없음)'}</p>
              {item.description && (
                <p className="text-[#8B9BB4] text-xs leading-relaxed">{item.description}</p>
              )}
              {quotes.length > 0 && (
                <div className="space-y-1 border-l-2 border-[#00E5CC]/30 pl-2.5">
                  {quotes.map((q, qi) => (
                    <p key={qi} className="text-[11px] text-[#8B9BB4] italic leading-snug">“{q}”</p>
                  ))}
                </div>
              )}
              {item.monitoring_point && (
                <p className="text-[11px] text-[#60A5FA] leading-snug">
                  <i className="ri-focus-3-line mr-1"></i>모니터링 포인트: {item.monitoring_point}
                </p>
              )}
              {item.work_note && (
                <p className="text-[11px] text-[#A78BFA] leading-snug">
                  <i className="ri-briefcase-4-line mr-1"></i>업무 참고: {item.work_note}
                </p>
              )}
              {item.publisher_url && (
                <a href={item.publisher_url} target="_blank" rel="noreferrer" className="inline-block text-[11px] text-[#00E5CC] hover:underline pt-1">
                  <i className="ri-external-link-line mr-0.5"></i>원문 링크
                </a>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function PersonasPanel({ personas }: { personas: Persona[] }) {
  if (personas.length === 0) return null;
  return (
    <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-6">
      <div className="flex items-center gap-2 mb-1">
        <h2 className="text-white font-bold text-base">Personas</h2>
        <span className="text-[10px] px-2 py-0.5 rounded bg-[#1E2530] text-[#8B9BB4]">audience targeting 메타데이터 — advisory, 게이팅 아님</span>
      </div>
      <p className="text-[#8B9BB4] text-xs mb-4">발송 대상 관점별 키워드·관심 신호 정의</p>
      <div className="space-y-4">
        {personas.map(p => (
          <div key={p.persona_id} className="bg-[#0D1117] border border-[#1E2530] rounded-xl p-3 space-y-2">
            <div className="flex items-center gap-2">
              <p className="text-white text-xs font-bold">{p.label || p.persona_id}</p>
              <span className="text-[9px] text-[#4A5568] font-mono">{p.persona_id}</span>
            </div>
            {p.description && <p className="text-[11px] text-[#8B9BB4] leading-snug">{p.description}</p>}
            {asStringArray(p.default_keywords).length > 0 && (
              <div className="flex flex-wrap items-center gap-1">
                {asStringArray(p.default_keywords).map(k => <Chip key={k} text={k} tone="bg-[#00E5CC]/10 text-[#00E5CC]" />)}
              </div>
            )}
            {asStringArray(p.priority_terms).length > 0 && (
              <div className="flex flex-wrap items-center gap-1">
                <span className="text-[9px] text-[#4A5568]">priority</span>
                {asStringArray(p.priority_terms).map(k => <Chip key={k} text={k} tone="bg-[#F59E0B]/10 text-[#F59E0B]" />)}
              </div>
            )}
            {asStringArray(p.watch_terms).length > 0 && (
              <div className="flex flex-wrap items-center gap-1">
                <span className="text-[9px] text-[#4A5568]">watch</span>
                {asStringArray(p.watch_terms).map(k => <Chip key={k} text={k} />)}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function ReviewerRolesPanel({ roles }: { roles: ReviewerRole[] }) {
  if (roles.length === 0) return null;
  return (
    <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-6">
      <div className="flex items-center gap-2 mb-1">
        <h2 className="text-white font-bold text-base">Reviewer Roles</h2>
        <span className="text-[10px] px-2 py-0.5 rounded bg-[#1E2530] text-[#8B9BB4]">advisory 리뷰 렌즈 — 승인 워크플로 아님</span>
      </div>
      <p className="text-[#8B9BB4] text-xs mb-4">기사 리뷰 소견을 생성하는 역할별 점검 항목</p>
      <div className="space-y-4">
        {roles.map(r => (
          <div key={r.role_id} className="bg-[#0D1117] border border-[#1E2530] rounded-xl p-3 space-y-2">
            <div className="flex items-center gap-2">
              <p className="text-white text-xs font-bold">{r.label || r.role_id}</p>
              <span className="text-[9px] text-[#4A5568] font-mono">{r.role_id}</span>
            </div>
            {r.description && <p className="text-[11px] text-[#8B9BB4] leading-snug">{r.description}</p>}
            {asStringArray(r.required_checks).length > 0 && (
              <div className="flex flex-wrap items-center gap-1">
                {asStringArray(r.required_checks).map(c => <Chip key={c} text={c} tone="bg-[#3B82F6]/10 text-[#60A5FA]" />)}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function RunRow({ run }: { run: KanbanRun }) {
  const keywords = parseJsonArray(run.keywords_json);
  const recipients = parseJsonArray(run.recipients_json);
  const qrStatus = run.quality_report?.status ?? null;
  const counts = run.counts ?? {};
  return (
    <tr className="border-b border-[#1E2530]/50 last:border-b-0 hover:bg-[#1E2530]/30 align-top">
      <td className="py-2.5 pr-3 whitespace-nowrap">
        <span className="text-white text-xs font-mono">{run.run_id}</span>
        {run.window_label && <p className="text-[#4A5568] text-[10px] mt-0.5">{run.window_label}</p>}
      </td>
      <td className="py-2.5 pr-3 text-[#8B9BB4] text-xs whitespace-nowrap">
        {run.generated_at ? new Date(run.generated_at).toLocaleString('ko-KR') : <Dash />}
      </td>
      <td className="py-2.5 pr-3 whitespace-nowrap">
        <span className={`text-[10px] px-2 py-0.5 rounded ${badgeTone(run.status)}`}>{run.status}</span>
      </td>
      <td className="py-2.5 pr-3 whitespace-nowrap">
        {qrStatus ? <span className={`text-[10px] px-2 py-0.5 rounded ${badgeTone(qrStatus)}`}>{qrStatus}</span> : <Dash />}
      </td>
      <td className="py-2.5 pr-3 whitespace-nowrap">
        <span className={`text-[10px] px-2 py-0.5 rounded ${DELIVERY_TONE(run.delivery_status)}`}>{run.delivery_status}</span>
      </td>
      <td className="py-2.5 pr-3 whitespace-nowrap">
        <span className={`text-[10px] px-2 py-0.5 rounded ${badgeTone(run.approval_status)}`}>{run.approval_status}</span>
      </td>
      <td className="py-2.5 pr-3 text-[#8B9BB4] text-xs whitespace-nowrap">
        발굴 {run.discovered_count} · 최신 {run.recent_count} · 선택 {run.selected_count}
      </td>
      <td className="py-2.5 pr-3 text-[#8B9BB4] text-xs whitespace-nowrap">
        {counts.needs_review ?? <Dash />} / {counts.ready_for_writer ?? <Dash />}
      </td>
      <td className="py-2.5 pr-3 text-[#8B9BB4] text-xs max-w-[220px]">
        <span className="block truncate">{keywords.length > 0 ? keywords.join(', ') : <Dash />}</span>
      </td>
      <td className="py-2.5 pr-3 text-[#8B9BB4] text-xs max-w-[180px]">
        <span className="block truncate">{run.owner_email ?? <Dash />}{recipients.length > 0 ? ` (+${recipients.length})` : ''}</span>
      </td>
      <td className="py-2.5 text-right whitespace-nowrap">
        {run.html_path
          ? <span className="text-[10px] text-[#4A5568]"><i className="ri-file-text-line mr-0.5"></i>{run.html_path.split('/').pop()}</span>
          : <Dash />}
      </td>
    </tr>
  );
}

export default function AdminDailyMailingKanbanPage() {
  const navigate = useNavigate();
  const [authChecked, setAuthChecked] = useState(false);
  const [data, setData] = useState<DailyMailingKanban | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const me = await fetchMe();
        if (!me || me.role !== 'admin') {
          navigate('/', { replace: true });
          return;
        }
        setAuthChecked(true);
      } catch {
        navigate('/login', { replace: true });
      }
    })();
  }, [navigate]);

  const reload = async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetchDailyMailingKanban();
      setData(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : '칸반 조회 실패');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (authChecked) reload();
  }, [authChecked]);

  if (!authChecked) {
    return (
      <div className="min-h-screen flex items-center justify-center text-[#8B9BB4] text-sm">
        <i className="ri-loader-4-line animate-spin mr-2"></i>권한 확인 중…
      </div>
    );
  }

  const latestRun = data?.runs?.[0] ?? null;
  const personas = data?.personas ?? [];
  const reviewerRoles = data?.reviewer_roles ?? [];
  const boardPurpose = data?.operating_policy?.board_purpose
    || '헤르메스 에이전트가 스콥을 검토·수집·작성·발송하는 파이프라인 현황 (Admin 전용, 읽기 전용 운영 보드).';

  return (
    <div className="min-h-screen bg-[#0D1117] text-white">
      <div className="px-8 pt-8 pb-6 border-b border-[#1E2530]">
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="w-5 h-5 flex items-center justify-center"><i className="ri-kanban-view text-[#00E5CC]"></i></span>
              <h1 className="text-2xl font-bold text-white">Daily Mailing — 운영 칸반</h1>
            </div>
            <p className="text-[#8B9BB4] text-sm max-w-3xl">{boardPurpose}</p>
          </div>
          <button onClick={reload} className="text-[#8B9BB4] text-xs hover:text-white cursor-pointer flex items-center gap-1 whitespace-nowrap">
            <i className="ri-refresh-line"></i>새로고침
          </button>
        </div>
      </div>

      <div className="px-8 py-6 space-y-5">
        {loading && (
          <div className="text-center py-16 text-sm text-[#8B9BB4]">
            <i className="ri-loader-4-line animate-spin mr-2"></i>칸반 로드 중…
          </div>
        )}
        {!loading && error && (
          <div className="text-center py-16">
            <span className="w-12 h-12 flex items-center justify-center mx-auto mb-3"><i className="ri-error-warning-line text-4xl text-red-400"></i></span>
            <p className="text-sm text-red-400">{error}</p>
            <button onClick={reload} className="mt-4 text-sm cursor-pointer hover:underline text-[#00E5CC]">다시 시도</button>
          </div>
        )}

        {!loading && !error && data && (
          <>
            {/* Summary */}
            <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5 flex flex-wrap items-center gap-4">
              <div className="flex items-center gap-2">
                <span className="w-8 h-8 flex items-center justify-center rounded-lg bg-[#00E5CC]/10 text-[#00E5CC]"><i className="ri-archive-line"></i></span>
                <div>
                  <p className="text-[#4A5568] text-[10px]">보존 기간</p>
                  <p className="text-white text-sm font-semibold">{data.retention_days}일</p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <span className="w-8 h-8 flex items-center justify-center rounded-lg bg-[#3B82F6]/10 text-[#60A5FA]"><i className="ri-play-list-line"></i></span>
                <div>
                  <p className="text-[#4A5568] text-[10px]">실행 건수</p>
                  <p className="text-white text-sm font-semibold">{data.runs.length}건</p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <span className="w-8 h-8 flex items-center justify-center rounded-lg bg-[#7C3AED]/10 text-[#A78BFA]"><i className="ri-file-list-3-line"></i></span>
                <div>
                  <p className="text-[#4A5568] text-[10px]">기사 총계</p>
                  <p className="text-white text-sm font-semibold">
                    {data.lanes.filter(l => ARTICLE_LANES.has(l.name)).reduce((sum, l) => sum + l.items.length, 0)}건
                  </p>
                </div>
              </div>
              <div className="ml-auto flex flex-wrap items-center gap-2">
                {data.operating_policy?.live_send_allowed != null && boolBadge('라이브 발송', data.operating_policy.live_send_allowed)}
                <span className="text-[10px] px-3 py-1.5 rounded-full bg-[#1E2530] text-[#8B9BB4] whitespace-nowrap">
                  <i className="ri-information-line mr-1"></i>
                  {data.article_approval_required ? '기사별 승인 필요' : '기사별 승인 없음 — 운영 보드'}
                </span>
              </div>
            </div>

            {/* Quality Report (latest run) */}
            {latestRun && <QualityReportPanel run={latestRun} />}

            {/* 오늘의 브리프 (latest run draft_items) */}
            {latestRun && <DraftBriefPanel run={latestRun} />}

            {/* Runs table */}
            <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-6">
              <h2 className="text-white font-bold text-base mb-4">실행(Run) 이력</h2>
              {data.runs.length === 0 ? (
                <p className="text-[#4A5568] text-sm">아직 실행 이력이 없습니다. 헤르메스 run 번들 동기화 후 표시됩니다.</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-[#8B9BB4] text-xs border-b border-[#1E2530]">
                        <th className="text-left py-2 pr-3">Run ID</th>
                        <th className="text-left py-2 pr-3">생성 시각</th>
                        <th className="text-left py-2 pr-3">상태</th>
                        <th className="text-left py-2 pr-3">품질</th>
                        <th className="text-left py-2 pr-3">발송 상태</th>
                        <th className="text-left py-2 pr-3">승인 상태</th>
                        <th className="text-left py-2 pr-3">건수</th>
                        <th className="text-left py-2 pr-3">검토/작성대기</th>
                        <th className="text-left py-2 pr-3">키워드</th>
                        <th className="text-left py-2 pr-3">수신</th>
                        <th className="text-right py-2">산출물</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.runs.map(r => <RunRow key={r.run_id} run={r} />)}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {/* Personas + Reviewer Roles */}
            {(personas.length > 0 || reviewerRoles.length > 0) && (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
                <PersonasPanel personas={personas} />
                <ReviewerRolesPanel roles={reviewerRoles} />
              </div>
            )}

            {/* 6-lane board */}
            <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-6">
              <h2 className="text-white font-bold text-base mb-1">파이프라인 레인</h2>
              <p className="text-[#8B9BB4] text-xs mb-4">
                Dashboard Scope → Source Intake → Triage/Verify → Writer Agent → Review Board → Delivery/History, 6단계 lane 별 현황
              </p>
              {data.lanes.length === 0 ? (
                <p className="text-[#4A5568] text-sm text-center py-8">레인 데이터가 없습니다.</p>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-3 lg:grid-cols-6 gap-4">
                  {data.lanes.map(lane => (
                    <div key={lane.name} className="bg-[#0D1117] border border-[#1E2530] rounded-xl p-3 flex flex-col min-h-[160px]">
                      <div className="flex items-center gap-1.5 mb-3">
                        <span className="w-5 h-5 flex items-center justify-center text-[#00E5CC]">
                          <i className={`${LANE_ICON[lane.name] ?? 'ri-stack-line'} text-sm`}></i>
                        </span>
                        <p className="text-white text-xs font-bold flex-1">{lane.name}</p>
                        <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-[#1E2530] text-[#8B9BB4]">{(lane.items ?? []).length}</span>
                      </div>
                      <div className="space-y-2 flex-1 overflow-y-auto max-h-[70vh]">
                        {(lane.items ?? []).length === 0 ? (
                          <p className="text-[#4A5568] text-xs text-center py-6">자료 없음</p>
                        ) : ARTICLE_LANES.has(lane.name) ? (
                          (lane.items ?? []).map(a => <ArticleCard key={`${a.run_id}_${a.article_id}`} article={a} />)
                        ) : (
                          (lane.items ?? []).map((item, idx) => (
                            <InfoCard key={(item as KanbanArticle).article_id ?? `${lane.name}_${idx}`} item={item as unknown as Record<string, unknown>} />
                          ))
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
