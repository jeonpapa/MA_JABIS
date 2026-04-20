import { Link } from 'react-router-dom';
import { useEffect, useState } from 'react';
import KeywordCloud from './components/KeywordCloud';
import MsdSummaryCards from './components/MsdSummaryCards';
import { fetchTopPriceChanges, type TopPriceChangeItem } from '@/api/homeMarket';
import { ApiError } from '@/api/client';

export default function HomePage() {
  const [topItems, setTopItems] = useState<TopPriceChangeItem[]>([]);
  const [topLoading, setTopLoading] = useState(true);
  const [topError, setTopError] = useState<string | null>(null);
  const [latestDate, setLatestDate] = useState<string | null>(null);
  const [prevDate, setPrevDate] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      setTopLoading(true);
      setTopError(null);
      try {
        const r = await fetchTopPriceChanges(10);
        setTopItems(r.items);
        setLatestDate(r.latestApplyDate);
        setPrevDate(r.prevApplyDate);
      } catch (err) {
        setTopError(err instanceof ApiError ? err.message : '조회 실패');
      } finally {
        setTopLoading(false);
      }
    })();
  }, []);

  return (
    <div className="min-h-screen bg-[#0D1117] text-white">
      {/* Header */}
      <div className="px-8 pt-8 pb-6 border-b border-[#1E2530]">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-white">Dashboard Overview</h1>
            <p className="text-[#8B9BB4] text-sm mt-1">2025년 4월 기준 · Market Access Intelligence Hub</p>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2 bg-[#161B27] border border-[#1E2530] rounded-lg px-3 py-2">
              <span className="w-2 h-2 rounded-full bg-[#00E5CC] animate-pulse flex-shrink-0"></span>
              <span className="text-[#8B9BB4] text-xs whitespace-nowrap">실시간 업데이트</span>
            </div>
            <button className="flex items-center gap-2 bg-[#00E5CC] text-[#0A0E1A] text-sm font-semibold px-4 py-2 rounded-lg cursor-pointer whitespace-nowrap hover:bg-[#00C9B1] transition-colors">
              <span className="w-4 h-4 flex items-center justify-center"><i className="ri-download-2-line text-sm"></i></span>
              리포트 내보내기
            </button>
          </div>
        </div>
      </div>

      <div className="px-8 py-6 space-y-6">

        {/* Section 1: 한국MSD 요약 카드 3종 — 최상단 */}
        <MsdSummaryCards />

        {/* Section 2: 미디어 키워드 클라우드 */}
        <KeywordCloud />

        {/* Section 3: 시장 동향 - 전달 대비 약가 변동 Top 10 */}
        <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] overflow-hidden">
          <div className="flex items-center justify-between px-6 py-4 border-b border-[#1E2530]">
            <div>
              <div className="flex items-center gap-2">
                <span className="w-5 h-5 flex items-center justify-center">
                  <i className="ri-bar-chart-box-line text-[#00E5CC]"></i>
                </span>
                <h3 className="text-white font-bold text-base">시장 동향 — 전달 대비 약가 변동 Top 10</h3>
              </div>
              <p className="text-[#8B9BB4] text-xs mt-0.5 ml-7">
                {latestDate && prevDate ? `${prevDate} → ${latestDate}` : '—'} · 변동률 절댓값 기준 정렬
              </p>
            </div>
            <Link
              to="/domestic-pricing"
              className="flex items-center gap-1 text-[#00E5CC] text-xs font-medium hover:text-[#00C9B1] transition-colors cursor-pointer whitespace-nowrap"
            >
              국내약가 전체 보기
              <span className="w-4 h-4 flex items-center justify-center"><i className="ri-arrow-right-line text-sm"></i></span>
            </Link>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="bg-[#1E2530]">
                  <th className="text-center text-[#8B9BB4] text-xs font-semibold px-4 py-3 w-10">순위</th>
                  <th className="text-left text-[#8B9BB4] text-xs font-semibold px-4 py-3">제품명</th>
                  <th className="text-left text-[#8B9BB4] text-xs font-semibold px-4 py-3">성분명</th>
                  <th className="text-left text-[#8B9BB4] text-xs font-semibold px-4 py-3">제약사</th>
                  <th className="text-right text-[#8B9BB4] text-xs font-semibold px-4 py-3">변동 전 (원)</th>
                  <th className="text-right text-[#8B9BB4] text-xs font-semibold px-4 py-3">현재 상한금액 (원)</th>
                  <th className="text-right text-[#8B9BB4] text-xs font-semibold px-4 py-3">변동액 (원)</th>
                  <th className="text-center text-[#8B9BB4] text-xs font-semibold px-4 py-3">변동률</th>
                  <th className="text-left text-[#8B9BB4] text-xs font-semibold px-4 py-3">비고</th>
                  <th className="text-left text-[#8B9BB4] text-xs font-semibold px-4 py-3">적용일</th>
                </tr>
              </thead>
              <tbody>
                {topLoading && (
                  <tr><td colSpan={10} className="py-8 text-center text-[#4A5568] text-sm">
                    <i className="ri-loader-4-line animate-spin text-[#00E5CC] mr-2"></i>불러오는 중...
                  </td></tr>
                )}
                {topError && (
                  <tr><td colSpan={10} className="py-8 text-center text-red-400 text-sm">{topError}</td></tr>
                )}
                {!topLoading && !topError && topItems.length === 0 && (
                  <tr><td colSpan={10} className="py-8 text-center text-[#4A5568] text-sm">변동 내역 없음</td></tr>
                )}
                {!topLoading && topItems.map((item, idx) => {
                  const rank = idx + 1;
                  return (
                    <tr
                      key={item.insurance_code}
                      className={`border-t border-[#1E2530] hover:bg-[#00E5CC]/5 transition-colors ${idx % 2 === 1 ? 'bg-[#1A2035]/20' : ''}`}
                    >
                      <td className="px-4 py-3 text-center">
                        <span className={`text-xs font-bold w-6 h-6 rounded-full flex items-center justify-center mx-auto ${
                          rank <= 3 ? 'bg-[#00E5CC]/20 text-[#00E5CC]' : 'text-[#4A5568]'
                        }`}>
                          {rank}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-white text-sm font-medium whitespace-nowrap">{item.brand_name || item.product_name}</td>
                      <td className="px-4 py-3 text-[#8B9BB4] text-sm whitespace-nowrap">{item.ingredient || '—'}</td>
                      <td className="px-4 py-3 text-[#8B9BB4] text-xs whitespace-nowrap">{item.company || '—'}</td>
                      <td className="px-4 py-3 text-[#8B9BB4] text-sm text-right whitespace-nowrap">{item.prev_price.toLocaleString()}</td>
                      <td className="px-4 py-3 text-white text-sm font-semibold text-right whitespace-nowrap">{item.curr_price.toLocaleString()}</td>
                      <td className={`px-4 py-3 text-sm font-semibold text-right whitespace-nowrap ${item.delta < 0 ? 'text-red-400' : 'text-emerald-400'}`}>
                        {item.delta > 0 ? '+' : ''}{item.delta.toLocaleString()}
                      </td>
                      <td className="px-4 py-3 text-center whitespace-nowrap">
                        <span className={`text-xs font-bold px-2 py-1 rounded-full ${
                          item.delta_pct < 0 ? 'text-red-400 bg-red-400/10' : 'text-emerald-400 bg-emerald-400/10'
                        }`}>
                          {item.delta_pct > 0 ? '+' : ''}{item.delta_pct}%
                        </span>
                      </td>
                      <td className="px-4 py-3 text-[#8B9BB4] text-xs whitespace-nowrap">
                        {item.remark
                          ? <span className="text-[#C9D1D9]">{item.remark}</span>
                          : <span className="text-[#4A5568]">—</span>}
                      </td>
                      <td className="px-4 py-3 text-[#4A5568] text-xs whitespace-nowrap">{latestDate ?? '—'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

      </div>
    </div>
  );
}
