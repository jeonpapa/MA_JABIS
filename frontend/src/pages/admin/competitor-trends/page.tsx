import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  listCompetitorTrends, refreshCompetitorTrends,
  type CompetitorTrend, type CompetitorRefreshResult,
} from '@/api/competitorTrends';
import CompetitorBrandsEditor from '@/components/competitor/CompetitorBrandsEditor';
import NewsFactorsEditor from '@/components/competitor/NewsFactorsEditor';
import { fetchMe } from '@/utils/authUsers';

/**
 * Admin — Competitor Trends 수집 관리 (B3 리포인트)
 *
 * 동향 카드는 자동 파이프라인(주간 크롤 + 매일 아카이브 승격 + LLM 필터)이 생성하므로
 * 수동 카드 추가/편집 UI 는 제거. admin 은 **스크래핑 필터·키워드**를 관리한다:
 *   1) 추적 브랜드/검색 쿼리 (competitor_brand)
 *   2) relevance 키워드 (news_keyword_factor scope=competitor/kind=relevance)
 *   3) 크롤 수동 트리거
 * 백엔드 CRUD 엔드포인트(POST/PATCH/DELETE /api/admin/competitor-trends*)와
 * source_type='manual' 보존 로직은 백필/교정용으로 유지 (API 직접 호출 가능).
 */
export default function AdminCompetitorTrendsPage() {
  const navigate = useNavigate();
  const [authChecked, setAuthChecked] = useState(false);
  const [items, setItems] = useState<CompetitorTrend[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showAudit, setShowAudit] = useState(false);

  const [refreshBusy, setRefreshBusy] = useState(false);
  const [refreshResult, setRefreshResult] = useState<CompetitorRefreshResult | null>(null);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [refreshDays, setRefreshDays] = useState(7);

  const handleRefresh = async (dryRun: boolean) => {
    if (refreshBusy) return;
    setRefreshBusy(true);
    setRefreshError(null);
    setRefreshResult(null);
    try {
      const r = await refreshCompetitorTrends({ days: refreshDays, dry_run: dryRun });
      setRefreshResult(r);
      if (!dryRun) await reload();
    } catch (e) {
      setRefreshError(e instanceof Error ? e.message : '크롤 실패');
    } finally {
      setRefreshBusy(false);
    }
  };

  useEffect(() => {
    (async () => {
      try {
        const me = await fetchMe();
        if (!me || me.role !== 'admin') { navigate('/', { replace: true }); return; }
        setAuthChecked(true);
      } catch {
        navigate('/login', { replace: true });
      }
    })();
  }, [navigate]);

  const reload = async () => {
    setLoading(true); setError(null);
    try { setItems(await listCompetitorTrends()); }
    catch (e) { setError(e instanceof Error ? e.message : '조회 실패'); }
    finally { setLoading(false); }
  };

  useEffect(() => { if (authChecked) reload(); }, [authChecked]);

  if (!authChecked) return null;

  return (
    <div className="min-h-screen bg-[#0D1117] text-white px-8 py-8">
      <div className="mb-6">
        <h1 className="text-2xl font-bold">Competitor Trends — 수집 관리</h1>
        <p className="text-[#8B9BB4] text-sm mt-1">
          동향 카드는 자동 생성(주간 크롤 + 매일 아카이브 승격 + LLM 필터·클러스터링).
          여기서는 <span className="text-[#00E5CC]">스크래핑 브랜드·relevance 키워드</span>를 관리합니다.
        </p>
      </div>

      {/* 자동 크롤 트리거 패널 */}
      <div className="bg-[#161B27] border border-[#1E2530] rounded-xl p-5 mb-6">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h2 className="text-sm font-semibold text-[#00E5CC]">자동 크롤 (Naver + GPT-4o-mini)</h2>
            <p className="text-[#8B9BB4] text-xs mt-0.5">
              아래 &lsquo;추적 브랜드&rsquo;에 등록된 브랜드 전체를 대상으로 지난 N일 뉴스 수집 → 매체 tier 분류(전문지 우선, 미등록 매체 제외) →
              LLM 중요도 필터 → 같은 이벤트 기사는 한 카드로 클러스터링(critical/moderate 만). manual 카드는 덮어쓰지 않음.
            </p>
          </div>
        </div>
        <div className="flex items-end gap-3">
          <div>
            <label className="block text-[#8B9BB4] text-[11px] mb-1">기간 (일)</label>
            <input
              type="number" min={1} max={30} value={refreshDays}
              onChange={e => setRefreshDays(Math.max(1, Math.min(30, Number(e.target.value) || 7)))}
              className="bg-[#0D1117] border border-[#1E2530] rounded px-3 py-2 text-sm w-24"
            />
          </div>
          <button
            onClick={() => handleRefresh(true)}
            disabled={refreshBusy}
            className="bg-[#1E2530] text-[#8B9BB4] text-sm font-semibold px-4 py-2 rounded-lg hover:text-white disabled:opacity-50"
          >
            {refreshBusy ? '…' : '드라이런'}
          </button>
          <button
            onClick={() => handleRefresh(false)}
            disabled={refreshBusy}
            className="bg-[#00E5CC] text-[#0A0E1A] text-sm font-semibold px-4 py-2 rounded-lg hover:bg-[#00C9B1] disabled:opacity-50"
          >
            {refreshBusy ? '크롤 중…' : '지금 크롤 실행'}
          </button>
        </div>
        {refreshError && <p className="text-red-400 text-xs mt-3">{refreshError}</p>}
        {refreshResult && (
          <div className="mt-4 bg-[#0D1117] border border-[#1E2530] rounded-lg p-3">
            <div className="text-[#8B9BB4] text-xs mb-2">
              결과 · 총 fetched={refreshResult.totals.fetched} · accepted={refreshResult.totals.accepted} · upserted={refreshResult.totals.upserted}
              {refreshResult.dry_run && <span className="ml-2 text-[#F59E0B]">[DRY-RUN]</span>}
            </div>
            <table className="w-full text-xs">
              <thead>
                <tr className="text-[#4A5568] border-b border-[#1E2530]">
                  <th className="text-left py-1 pr-3">브랜드</th>
                  <th className="text-right py-1 pr-3">fetched</th>
                  <th className="text-right py-1 pr-3">accepted</th>
                  <th className="text-right py-1 pr-3">upserted</th>
                  <th className="text-right py-1">skipped_low</th>
                </tr>
              </thead>
              <tbody>
                {refreshResult.brands.map(b => (
                  <tr key={b.brand} className="border-b border-[#1E2530]/50 last:border-b-0">
                    <td className="py-1 pr-3 text-white">{b.brand} <span className="text-[#4A5568]">· {b.company}</span></td>
                    <td className="py-1 pr-3 text-right text-[#8B9BB4]">{b.fetched}</td>
                    <td className="py-1 pr-3 text-right text-[#00E5CC]">{b.accepted}</td>
                    <td className="py-1 pr-3 text-right text-[#3B82F6]">{b.upserted}</td>
                    <td className="py-1 text-right text-[#4A5568]">{b.skipped_low}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* 1) 추적 브랜드/검색 쿼리 편집 (competitor_brand) */}
      <div className="bg-[#161B27] border border-[#1E2530] rounded-xl p-5 mb-6">
        <h2 className="text-sm font-semibold text-[#00E5CC] mb-1">추적 브랜드 / 검색 쿼리</h2>
        <p className="text-[#8B9BB4] text-xs mb-4">
          Naver 뉴스 검색에 사용되는 브랜드 레지스트리 (competitor_brand). 비활성화하면 크롤 대상에서 제외됩니다.
        </p>
        <CompetitorBrandsEditor />
      </div>

      {/* 2) relevance 키워드 편집 (news_keyword_factor) */}
      <div className="bg-[#161B27] border border-[#1E2530] rounded-xl p-5 mb-6">
        <h2 className="text-sm font-semibold text-[#00E5CC] mb-1">뉴스 키워드 팩터 (relevance)</h2>
        <p className="text-[#8B9BB4] text-xs mb-4">
          경쟁사 relevance 키워드(scope=competitor / kind=relevance)는 수집·카드 승격 필터가 실제로 소비합니다 —
          브랜드가 등장해도 이 키워드가 하나도 없는 기사(주가·행사성)는 제외.
        </p>
        <NewsFactorsEditor />
      </div>

      {/* 3) 생성된 카드 — 읽기 전용 감사 목록 (수동 추가/편집 UI 는 제거됨) */}
      <div className="bg-[#161B27] border border-[#1E2530] rounded-xl p-5">
        <button
          onClick={() => setShowAudit(!showAudit)}
          className="w-full flex items-center justify-between cursor-pointer"
        >
          <div className="text-left">
            <h2 className="text-sm font-semibold text-[#00E5CC]">생성된 카드 (읽기 전용 감사)</h2>
            <p className="text-[#8B9BB4] text-xs mt-0.5">
              자동 파이프라인이 생성한 동향 카드 {items.length}건. 수동 추가/편집은 UI 에서 제거 — 교정이 필요하면 API 로.
            </p>
          </div>
          <i className={`text-lg text-[#8B9BB4] ${showAudit ? 'ri-arrow-up-s-line' : 'ri-arrow-down-s-line'}`}></i>
        </button>

        {showAudit && (
          loading ? (
            <div className="text-center py-8 text-[#8B9BB4]">로딩 중…</div>
          ) : error ? (
            <div className="text-center py-6 text-[#EF4444]">{error}</div>
          ) : (
            <div className="mt-4 space-y-2">
              {items.map(it => (
                <div key={it.id} className="bg-[#0D1117] border border-[#1E2530] rounded-xl p-3 flex items-start gap-3">
                  <div className="w-8 h-8 rounded-lg flex items-center justify-center text-[11px] font-bold flex-shrink-0"
                    style={{ backgroundColor: (it.color || '#1E2530') + '25', border: `1px solid ${(it.color || '#1E2530')}40` }}>
                    {it.logo || it.company.slice(0, 2).toUpperCase()}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-0.5 flex-wrap">
                      <span className="text-xs text-[#8B9BB4]">{it.company}</span>
                      <span className={`text-[11px] px-2 py-0.5 rounded-full ${it.badgeColor || 'bg-[#1E2530] text-[#8B9BB4]'}`}>{it.badge}</span>
                      <span className="text-xs text-[#4A5568]">{it.date}</span>
                      <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${
                        it.source_type === 'manual' ? 'bg-amber-500/20 text-amber-400' : 'bg-[#1E2530] text-[#8B9BB4]'
                      }`}>
                        {it.source_type || 'manual'}
                      </span>
                      {it.source_tier != null && (
                        <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-[#00857C]/20 text-[#00E5CC]">T{it.source_tier}</span>
                      )}
                      {(it.source_count ?? 0) > 1 && (
                        <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-400">{it.source_count}개 매체</span>
                      )}
                    </div>
                    <p className="text-sm font-semibold truncate">{it.headline}</p>
                    {it.url && (
                      <a href={it.url} target="_blank" rel="noopener noreferrer"
                        className="text-[11px] text-[#4A5568] hover:text-[#8B9BB4] hover:underline truncate block">
                        {it.source || it.url}
                      </a>
                    )}
                  </div>
                </div>
              ))}
              {items.length === 0 && <p className="text-center py-8 text-[#4A5568]">생성된 카드가 없습니다</p>}
            </div>
          )
        )}
      </div>
    </div>
  );
}
