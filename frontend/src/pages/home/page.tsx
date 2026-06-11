import { useState } from 'react';
import { Link } from 'react-router-dom';
import KeywordCloud from './components/KeywordCloud';
import MsdSummaryCards from './components/MsdSummaryCards';
import { fetchTopPriceChanges, applyDateToYmLabel } from '@/api/home';
import { useApi } from '@/hooks/useApi';

export default function HomePage() {
  const [isDark, setIsDark] = useState(false);

  const { data: topPrice, loading: topLoading, error: topError } = useApi(() => fetchTopPriceChanges(10), []);
  const topItems = topPrice?.items ?? [];
  const ymLabel = applyDateToYmLabel(topPrice?.latestApplyDate ?? null);

  const pageBg = isDark ? 'bg-[#0D1117]' : 'bg-gray-50';
  const headerBorder = isDark ? 'border-[#1E2530]' : 'border-gray-200';
  const sectionBg = isDark ? 'bg-[#161B27]' : 'bg-white';
  const sectionBorder = isDark ? 'border-[#1E2530]' : 'border-gray-200';
  const tableHeaderBg = isDark ? 'bg-[#1E2530]' : 'bg-gray-100';
  const tableBorder = isDark ? 'border-[#1E2530]' : 'border-gray-200';
  const tableStripe = isDark ? 'bg-[#1A2035]/20' : 'bg-gray-50/50';
  const tableHover = isDark ? 'hover:bg-[#00E5CC]/5' : 'hover:bg-teal-50/50';
  const textMain = isDark ? 'text-white' : 'text-gray-900';
  const textSub = isDark ? 'text-[#8B9BB4]' : 'text-gray-500';
  const textMuted = isDark ? 'text-[#4A5568]' : 'text-gray-400';
  const accentTeal = isDark ? 'text-[#00E5CC]' : 'text-teal-600';
  const accentTealBg = isDark ? 'bg-[#00E5CC]/20' : 'bg-teal-100';
  const btnBg = isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-white border-gray-200';
  const badgeRankBg = isDark ? 'bg-[#00E5CC]/20 text-[#00E5CC]' : 'bg-teal-100 text-teal-700';
  const pillBg = isDark ? 'bg-[#161B27] border border-[#1E2530]' : 'bg-white border border-gray-200';
  const pillHover = isDark ? 'hover:border-[#2A3545]' : 'hover:border-gray-300';

  return (
    <div className={`min-h-screen ${pageBg} ${isDark ? 'text-white' : 'text-gray-900'}`}>
      {/* Header */}
      <div className={`px-8 pt-8 pb-6 border-b ${headerBorder}`}>
        <div className="flex items-center justify-between">
          <div>
            <h1 className={`text-2xl font-bold ${textMain}`}>Dashboard Overview</h1>
            <p className={`${textSub} text-sm mt-1`}>{ymLabel ? `${ymLabel} 기준 · ` : ''}Market Access Intelligence Hub</p>
          </div>
          <div className="flex items-center gap-3">
            <div className={`flex items-center gap-2 ${btnBg} rounded-lg px-3 py-2`}>
              <span className="w-2 h-2 rounded-full bg-teal-500 animate-pulse flex-shrink-0"></span>
              <span className={`${textSub} text-xs whitespace-nowrap`}>실시간 업데이트</span>
            </div>
            {/* Theme Toggle */}
            <button
              onClick={() => setIsDark(!isDark)}
              className={`w-9 h-9 flex items-center justify-center rounded-lg cursor-pointer transition-all ${
                isDark
                  ? 'bg-[#1E2530] text-amber-400 hover:bg-[#2A3545]'
                  : 'bg-gray-100 text-gray-500 hover:bg-gray-200'
              }`}
              title={isDark ? '라이트 모드' : '다크 모드'}
            >
              <i className={isDark ? 'ri-sun-line text-lg' : 'ri-moon-line text-lg'}></i>
            </button>
          </div>
        </div>
      </div>

      <div className="px-8 py-6 space-y-6">

        {/* Section 1: 한국MSD 요약 카드 3종 — 최상단 */}
        <MsdSummaryCards isDark={isDark} />

        {/* Section 2: 미디어 키워드 클라우드 */}
        <KeywordCloud isDark={isDark} />

        {/* Section 3: 시장 동향 - 전달 대비 약가 변동 Top 10 */}
        <div className={`${sectionBg} rounded-2xl border ${sectionBorder} overflow-hidden`}>
          <div className={`flex items-center justify-between px-6 py-4 border-b ${tableBorder}`}>
            <div>
              <div className="flex items-center gap-2">
                <span className={`w-5 h-5 flex items-center justify-center ${accentTeal}`}>
                  <i className="ri-bar-chart-box-line"></i>
                </span>
                <h3 className={`font-bold text-base ${textMain}`}>시장 동향 — 전달 대비 약가 변동 Top 10</h3>
              </div>
              <p className={`${textSub} text-xs mt-0.5 ml-7`}>
                {topPrice?.prevApplyDate && topPrice?.latestApplyDate
                  ? `${topPrice.prevApplyDate} → ${topPrice.latestApplyDate} 고시 기준`
                  : '—'}{' '}
                · 변동률 절댓값 기준 정렬
              </p>
            </div>
            <Link
              to="/domestic-pricing"
              className={`flex items-center gap-1 ${accentTeal} text-xs font-medium hover:text-teal-700 transition-colors cursor-pointer whitespace-nowrap`}
            >
              국내약가 전체 보기
              <span className="w-4 h-4 flex items-center justify-center"><i className="ri-arrow-right-line text-sm"></i></span>
            </Link>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className={tableHeaderBg}>
                  <th className={`text-center ${textSub} text-xs font-semibold px-4 py-3 w-10`}>순위</th>
                  <th className={`text-left ${textSub} text-xs font-semibold px-4 py-3`}>제품명</th>
                  <th className={`text-left ${textSub} text-xs font-semibold px-4 py-3`}>성분명</th>
                  <th className={`text-left ${textSub} text-xs font-semibold px-4 py-3`}>제약사</th>
                  <th className={`text-right ${textSub} text-xs font-semibold px-4 py-3`}>변동 전 (원)</th>
                  <th className={`text-right ${textSub} text-xs font-semibold px-4 py-3`}>현재 상한금액 (원)</th>
                  <th className={`text-right ${textSub} text-xs font-semibold px-4 py-3`}>변동액 (원)</th>
                  <th className={`text-center ${textSub} text-xs font-semibold px-4 py-3`}>변동률</th>
                  <th className={`text-left ${textSub} text-xs font-semibold px-4 py-3`}>비고</th>
                  <th className={`text-left ${textSub} text-xs font-semibold px-4 py-3`}>적용일</th>
                </tr>
              </thead>
              <tbody>
                {topLoading && (
                  <tr>
                    <td colSpan={10} className={`py-8 text-center ${textMuted} text-sm`}>
                      <i className={`ri-loader-4-line animate-spin ${accentTeal} mr-2`}></i>불러오는 중...
                    </td>
                  </tr>
                )}
                {!topLoading && topError && (
                  <tr>
                    <td colSpan={10} className="py-8 text-center text-red-500 text-sm">
                      약가 변동 조회 실패 — {topError}
                    </td>
                  </tr>
                )}
                {!topLoading && !topError && topItems.length === 0 && (
                  <tr>
                    <td colSpan={10} className={`py-8 text-center ${textMuted} text-sm`}>변동 내역 없음</td>
                  </tr>
                )}
                {!topLoading && !topError && topItems.map((item, idx) => (
                  <tr
                    key={item.rank}
                    className={`border-t ${tableBorder} ${tableHover} transition-colors ${idx % 2 === 1 ? tableStripe : ''}`}
                  >
                    <td className="px-4 py-3 text-center">
                      <span className={`text-xs font-bold w-6 h-6 rounded-full flex items-center justify-center mx-auto ${
                        item.rank <= 3 ? badgeRankBg : textMuted
                      }`}>
                        {item.rank}
                      </span>
                    </td>
                    <td className={`px-4 py-3 ${textMain} text-sm font-medium whitespace-nowrap`}>{item.productName || '—'}</td>
                    <td className={`px-4 py-3 ${textSub} text-sm whitespace-nowrap max-w-[220px] truncate`} title={item.ingredient}>{item.ingredient || '—'}</td>
                    <td className={`px-4 py-3 ${textSub} text-xs whitespace-nowrap`}>{item.company || '—'}</td>
                    <td className={`px-4 py-3 ${textSub} text-sm text-right whitespace-nowrap`}>{item.prevPrice.toLocaleString()}</td>
                    <td className={`px-4 py-3 ${textMain} text-sm font-semibold text-right whitespace-nowrap`}>{item.currPrice.toLocaleString()}</td>
                    <td className={`px-4 py-3 text-sm font-semibold text-right whitespace-nowrap ${item.changeAmt < 0 ? 'text-red-500' : 'text-emerald-500'}`}>
                      {item.changeAmt > 0 ? '+' : ''}{item.changeAmt.toLocaleString()}
                    </td>
                    <td className="px-4 py-3 text-center whitespace-nowrap">
                      <span className={`text-xs font-bold px-2 py-1 rounded-full ${
                        item.changeRate < 0 ? 'text-red-500 bg-red-50 border border-red-200' : 'text-emerald-500 bg-emerald-50 border border-emerald-200'
                      }`}>
                        {item.changeRate > 0 ? '+' : ''}{item.changeRate}%
                      </span>
                    </td>
                    <td className={`px-4 py-3 ${textSub} text-xs whitespace-nowrap`}>{item.reason || '—'}</td>
                    <td className={`px-4 py-3 ${textMuted} text-xs whitespace-nowrap`}>{item.date || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

      </div>
    </div>
  );
}