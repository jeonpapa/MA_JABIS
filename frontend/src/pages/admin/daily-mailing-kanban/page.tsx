import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { fetchMe } from '@/utils/authUsers';
import {
  fetchDailyMailingKanban, parseJsonArray,
  type DailyMailingKanban, type KanbanArticle, type KanbanRun,
} from '@/api/dailyMailingKanban';

const LANE_ICON: Record<string, string> = {
  'Dashboard Scope': 'ri-crosshair-2-line',
  'Source Intake': 'ri-inbox-archive-line',
  'Triage/Verify': 'ri-shield-check-line',
  'Writer Agent': 'ri-quill-pen-line',
  'Delivery/History': 'ri-send-plane-2-line',
};

const Dash = () => <span className="text-[#4A5568]">—</span>;

function badgeTone(value: string | null | undefined): string {
  if (!value) return 'bg-[#1E2530] text-[#8B9BB4]';
  const v = value.toLowerCase();
  if (['verified', 'official_verified', 'publisher_verified', 'high', 'included', 'tier1', 'tier_1'].some(k => v.includes(k))) {
    return 'bg-[#00E5CC]/10 text-[#00E5CC]';
  }
  if (['excluded', 'rejected', 'low'].some(k => v.includes(k))) {
    return 'bg-red-500/10 text-red-400';
  }
  if (['pending', 'unverified', 'medium', 'tier2', 'tier_2'].some(k => v.includes(k))) {
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

function ArticleCard({ article }: { article: KanbanArticle }) {
  return (
    <div className="bg-[#0D1117] border border-[#1E2530] rounded-xl p-3 space-y-2">
      <p className="text-white text-xs font-semibold leading-snug line-clamp-3">{article.title}</p>
      <div className="flex flex-wrap items-center gap-1.5">
        {article.source_name && (
          <span className="text-[10px] px-2 py-0.5 rounded bg-[#1E2530] text-[#8B9BB4]">{article.source_name}</span>
        )}
        {article.source_tier && (
          <span className={`text-[10px] px-2 py-0.5 rounded ${badgeTone(article.source_tier)}`}>{article.source_tier}</span>
        )}
        {article.source_status && (
          <span className={`text-[10px] px-2 py-0.5 rounded ${badgeTone(article.source_status)}`}>{article.source_status}</span>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {article.priority && (
          <span className={`text-[10px] px-2 py-0.5 rounded ${badgeTone(article.priority)}`}>우선순위: {article.priority}</span>
        )}
        {article.ma_relevance != null && (
          <span className="text-[10px] px-2 py-0.5 rounded bg-[#7C3AED]/10 text-[#A78BFA]">MA 연관도 {article.ma_relevance}</span>
        )}
        {!!article.selected_for_draft && (
          <span className="text-[10px] px-2 py-0.5 rounded bg-[#00E5CC]/10 text-[#00E5CC]">초안 선택됨</span>
        )}
      </div>
      {article.quality_flags.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          {article.quality_flags.map(f => (
            <span key={f} className="text-[10px] px-2 py-0.5 rounded bg-red-500/10 text-red-400">
              <i className="ri-flag-2-line mr-0.5"></i>{f}
            </span>
          ))}
        </div>
      )}
      {article.matched_keywords.length > 0 && (
        <div className="flex flex-wrap items-center gap-1">
          {article.matched_keywords.slice(0, 5).map(k => (
            <span key={k} className="text-[10px] px-1.5 py-0.5 rounded bg-[#1E2530] text-[#4A5568]">{k}</span>
          ))}
        </div>
      )}
      {article.verification_caveat && (
        <p className="text-[10px] text-[#F59E0B] leading-snug">
          <i className="ri-error-warning-line mr-1"></i>{article.verification_caveat}
        </p>
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
        {article.published_at && (
          <span className="text-[10px] text-[#4A5568] ml-auto whitespace-nowrap">{article.published_at}</span>
        )}
      </div>
    </div>
  );
}

function RunRow({ run }: { run: KanbanRun }) {
  const keywords = parseJsonArray(run.keywords_json);
  const recipients = parseJsonArray(run.recipients_json);
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
        <span className={`text-[10px] px-2 py-0.5 rounded ${DELIVERY_TONE(run.delivery_status)}`}>{run.delivery_status}</span>
      </td>
      <td className="py-2.5 pr-3 whitespace-nowrap">
        <span className={`text-[10px] px-2 py-0.5 rounded ${badgeTone(run.approval_status)}`}>{run.approval_status}</span>
      </td>
      <td className="py-2.5 pr-3 text-[#8B9BB4] text-xs whitespace-nowrap">
        발굴 {run.discovered_count} · 최신 {run.recent_count} · 선택 {run.selected_count}
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

  return (
    <div className="min-h-screen bg-[#0D1117] text-white">
      <div className="px-8 pt-8 pb-6 border-b border-[#1E2530]">
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="w-5 h-5 flex items-center justify-center"><i className="ri-kanban-view text-[#00E5CC]"></i></span>
              <h1 className="text-2xl font-bold text-white">Daily Mailing — 운영 칸반</h1>
            </div>
            <p className="text-[#8B9BB4] text-sm">
              헤르메스 에이전트가 스콥을 검토·수집·작성·발송하는 파이프라인 현황 (Admin 전용, 읽기 전용 운영 보드).
            </p>
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
                  <p className="text-white text-sm font-semibold">{data.lanes.reduce((sum, l) => sum + l.items.length, 0)}건</p>
                </div>
              </div>
              <span className="ml-auto text-[10px] px-3 py-1.5 rounded-full bg-[#1E2530] text-[#8B9BB4] whitespace-nowrap">
                <i className="ri-information-line mr-1"></i>
                {data.article_approval_required ? '기사별 승인 필요' : '기사별 승인 없음 — 운영 보드'}
              </span>
            </div>

            {/* Runs table */}
            <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-6">
              <h2 className="text-white font-bold text-base mb-4">실행(Run) 이력</h2>
              {data.runs.length === 0 ? (
                <p className="text-[#4A5568] text-sm">아직 실행 이력이 없습니다.</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-[#8B9BB4] text-xs border-b border-[#1E2530]">
                        <th className="text-left py-2 pr-3">Run ID</th>
                        <th className="text-left py-2 pr-3">생성 시각</th>
                        <th className="text-left py-2 pr-3">상태</th>
                        <th className="text-left py-2 pr-3">발송 상태</th>
                        <th className="text-left py-2 pr-3">승인 상태</th>
                        <th className="text-left py-2 pr-3">건수</th>
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

            {/* Lanes */}
            <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-6">
              <h2 className="text-white font-bold text-base mb-1">파이프라인 레인</h2>
              <p className="text-[#8B9BB4] text-xs mb-4">스콥 → 소스 수집 → 검증 → 작성 → 발송/이력, 5단계 lane 별 기사 현황</p>
              <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
                {data.lanes.map(lane => (
                  <div key={lane.name} className="bg-[#0D1117] border border-[#1E2530] rounded-xl p-3 flex flex-col min-h-[160px]">
                    <div className="flex items-center gap-1.5 mb-3">
                      <span className="w-5 h-5 flex items-center justify-center text-[#00E5CC]">
                        <i className={`${LANE_ICON[lane.name] ?? 'ri-stack-line'} text-sm`}></i>
                      </span>
                      <p className="text-white text-xs font-bold flex-1">{lane.name}</p>
                      <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-[#1E2530] text-[#8B9BB4]">{lane.items.length}</span>
                    </div>
                    <div className="space-y-2 flex-1 overflow-y-auto max-h-[70vh]">
                      {lane.items.length === 0 ? (
                        <p className="text-[#4A5568] text-xs text-center py-6">자료 없음</p>
                      ) : (
                        lane.items.map(a => <ArticleCard key={a.article_id} article={a} />)
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
