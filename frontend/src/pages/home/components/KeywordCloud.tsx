import { useState } from 'react';
import {
  fetchMediaIntelligence,
  fetchGovKeywordSummary,
  fetchBrandNews,
  type BrandTrafficView,
  type BrandNewsView,
} from '@/api/home';
import { useApi } from '@/hooks/useApi';

type BrandItem = BrandTrafficView;

interface Props {
  isDark: boolean;
}

const TAG_COLORS: Record<string, string> = {
  '급여': 'bg-emerald-400/10 text-emerald-400 border border-emerald-400/20',
  '약가': 'bg-red-400/10 text-red-400 border border-red-400/20',
  '허가': 'bg-amber-400/10 text-amber-400 border border-amber-400/20',
  '임상': 'bg-violet-400/10 text-violet-400 border border-violet-400/20',
  '시장': 'bg-teal-400/10 text-teal-400 border border-teal-400/20',
  '매출': 'bg-sky-400/10 text-sky-400 border border-sky-400/20',
};

const getHeatColor = (trafficIndex: number, maxTraffic: number): string => {
  const ratio = maxTraffic ? trafficIndex / maxTraffic : 0;
  if (ratio >= 0.9) return '#EF4444';
  if (ratio >= 0.7) return '#F97316';
  if (ratio >= 0.5) return '#D97706';
  if (ratio >= 0.3) return '#0D9488';
  return '#9CA3AF';
};

const getHeatClass = (trafficIndex: number, maxTraffic: number): string => {
  const ratio = maxTraffic ? trafficIndex / maxTraffic : 0;
  if (ratio >= 0.9) return 'text-red-500';
  if (ratio >= 0.7) return 'text-orange-500';
  if (ratio >= 0.5) return 'text-amber-500';
  if (ratio >= 0.3) return 'text-teal-500';
  return 'text-gray-400';
};

export default function KeywordCloud({ isDark }: Props) {
  const [selectedBrand, setSelectedBrand] = useState<BrandItem | null>(null);
  const [selectedKeyword, setSelectedKeyword] = useState<string | null>(null);
  const [drawerNews, setDrawerNews] = useState<BrandNewsView[]>([]);
  const [drawerLoading, setDrawerLoading] = useState(false);

  // 좌측(정부 키워드 — LLM 기반, 느리거나 실패 가능)과 우측(미디어)은 독립 로딩
  const { data: media, loading: mediaLoading, error: mediaError } = useApi(fetchMediaIntelligence, []);
  const { data: gov, loading: govLoading, error: govError } = useApi(fetchGovKeywordSummary, []);

  const keywordCloudData = gov?.keywords ?? [];
  const brandTrafficData = media?.brands ?? [];

  const maxWeight = keywordCloudData.length > 0 ? Math.max(...keywordCloudData.map(k => k.weight)) : 1;
  const maxTraffic = brandTrafficData.length > 0 ? Math.max(...brandTrafficData.map(b => b.trafficIndex)) : 1;

  const getCloudStyle = (weight: number) => {
    const ratio = weight / maxWeight;
    if (ratio >= 0.9) return { size: 'text-[22px] font-black', opacity: 1.0 };
    if (ratio >= 0.78) return { size: 'text-lg font-bold', opacity: 0.95 };
    if (ratio >= 0.65) return { size: 'text-base font-bold', opacity: 0.88 };
    if (ratio >= 0.52) return { size: 'text-sm font-semibold', opacity: 0.78 };
    if (ratio >= 0.42) return { size: 'text-[13px] font-medium', opacity: 0.65 };
    return { size: 'text-xs font-normal', opacity: 0.5 };
  };

  const handleSelectBrand = async (brand: BrandItem) => {
    if (selectedBrand?.rank === brand.rank) {
      setSelectedBrand(null);
      setDrawerNews([]);
      return;
    }
    setSelectedBrand(brand);
    setDrawerNews(brand.news); // 수집 캐시분 즉시 노출
    setDrawerLoading(true);
    try {
      const fresh = await fetchBrandNews(brand.brand, 10);
      if (fresh.length > 0) setDrawerNews(fresh);
    } catch {
      // 실패 시 초기 캐시분 유지
    } finally {
      setDrawerLoading(false);
    }
  };

  const top1 = brandTrafficData[0];
  const selectedNews = selectedKeyword ? (gov?.newsByKeyword[selectedKeyword] || []) : [];
  const periodLabel = media?.days ? `지난 ${media.days}일 기준` : '지난 1개월 기준';

  const cardBg = isDark ? 'bg-[#161B27]' : 'bg-white';
  const cardBorder = isDark ? 'border-[#1E2530]' : 'border-gray-200';
  const textMain = isDark ? 'text-white' : 'text-gray-900';
  const textSub = isDark ? 'text-[#8B9BB4]' : 'text-gray-500';
  const textMuted = isDark ? 'text-[#4A5568]' : 'text-gray-400';
  const textDimmed = isDark ? 'text-[#2A3545]' : 'text-gray-300';
  const textBody = isDark ? 'text-[#C9D1D9]' : 'text-gray-700';
  const accentTeal = isDark ? 'text-[#00E5CC]' : 'text-teal-600';
  const accentAmber = isDark ? 'text-[#F59E0B]' : 'text-amber-600';
  const sectionBg = isDark ? 'bg-[#0D1117]' : 'bg-gray-50';
  const hoverBg = isDark ? 'hover:bg-[#1E2530]/50' : 'hover:bg-gray-100/80';
  const selectedBg = isDark ? 'bg-[#1E2530]' : 'bg-gray-100';
  const hotBg = isDark ? 'bg-[#EF4444]/10 border-[#EF4444]/20' : 'bg-red-50 border-red-200';
  const hotText = isDark ? 'text-[#EF4444]' : 'text-red-600';
  const newsBg = isDark ? 'bg-[#1A2035]/80 border-[#1E2530]' : 'bg-gray-50 border-gray-200';
  const newsHover = isDark ? 'hover:border-[#00E5CC]/30 hover:bg-[#1E2530]/60' : 'hover:border-teal-300 hover:bg-gray-100';
  const glowCenter = isDark ? 'bg-[#00E5CC]/3' : 'bg-teal-100/30';
  const barTrack = isDark ? 'bg-[#0D1117]' : 'bg-gray-200';
  const emptyIconBg = isDark ? 'bg-[#1E2530]' : 'bg-gray-100';
  const emptyIconText = isDark ? 'text-[#2A3545]' : 'text-gray-400';
  const upBadge = isDark ? 'bg-emerald-400/15 border-emerald-400/30' : 'bg-emerald-50 border-emerald-200';
  const downBadge = isDark ? 'bg-red-400/15 border-red-400/30' : 'bg-red-50 border-red-200';

  return (
    <div className={`${cardBg} rounded-2xl border ${cardBorder} overflow-hidden`}>
      {/* Header */}
      <div className={`flex items-center gap-2 px-5 pt-4 pb-3 border-b ${cardBorder}`}>
        <span className={`w-5 h-5 flex items-center justify-center ${accentTeal}`}>
          <i className="ri-cloud-line text-base"></i>
        </span>
        <h3 className={`font-bold text-sm ${textMain}`}>미디어 인텔리전스</h3>
        {top1 && (
          <div className={`flex items-center gap-1.5 ml-3 rounded-full px-2.5 py-0.5 border ${hotBg}`}>
            <span className={`w-3 h-3 flex items-center justify-center ${hotText}`}><i className="ri-fire-fill text-xs"></i></span>
            <span className={`text-xs font-semibold ${hotText}`}>HOT</span>
            <span className={`text-xs font-bold ${hotText}`}>{top1.brand}</span>
            <span className={`text-xs ${isDark ? 'text-[#FF7A00]/70' : 'text-orange-400'}`}>{top1.company}</span>
          </div>
        )}
        <span className={`ml-auto text-xs ${textMuted}`}>{periodLabel}</span>
      </div>

      <div className="grid grid-cols-5">
        {/* ── 왼쪽: 정부 키워드 클라우드 + 뉴스 카드 ── */}
        <div className={`col-span-2 border-r ${cardBorder} relative overflow-hidden flex flex-col`}>
          <div className="absolute inset-0 pointer-events-none">
            <div className={`absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-48 h-48 rounded-full ${glowCenter} blur-3xl`}></div>
          </div>

          <div className={`flex items-center gap-2 px-5 py-3 border-b ${cardBorder} relative z-10 flex-shrink-0`}>
            <span className={`w-4 h-4 flex items-center justify-center ${textSub}`}>
              <i className="ri-government-line text-xs"></i>
            </span>
            <p className={`${textSub} text-xs font-semibold`}>정부 기관 키워드</p>
            <span className={`${textDimmed} text-xs`}>보건복지부 · 건보공단 · 심평원</span>
            {selectedKeyword && (
              <button
                onClick={() => setSelectedKeyword(null)}
                className={`ml-auto w-5 h-5 flex items-center justify-center ${textMuted} hover:${textMain} cursor-pointer transition-colors rounded`}
              >
                <i className="ri-close-line text-sm"></i>
              </button>
            )}
          </div>

          <div className="relative z-10 w-full flex-1 flex items-center justify-center min-h-[180px]">
            {govLoading && (
              <p className={`${textMuted} text-xs flex items-center gap-2`}>
                <i className={`ri-loader-4-line animate-spin ${accentTeal}`}></i>
                AI 키워드 분석 중... (최대 수 분 소요)
              </p>
            )}
            {!govLoading && govError && (
              <p className="text-red-500 text-xs px-6 text-center leading-relaxed">
                정부 키워드 요약 실패 — {govError}
              </p>
            )}
            {!govLoading && !govError && gov?.error && (
              <p className="text-red-500 text-xs px-6 text-center leading-relaxed">{gov.error}</p>
            )}
            {!govLoading && !govError && !gov?.error && keywordCloudData.length === 0 && (
              <p className={`${textMuted} text-xs`}>키워드 정보 없음</p>
            )}
            {!govLoading && !govError && keywordCloudData.length > 0 && (
              <div className="flex flex-wrap gap-x-3 gap-y-2 items-center justify-center px-6 py-4 max-w-full">
                {keywordCloudData.map((kw, idx) => {
                  const style = getCloudStyle(kw.weight);
                  const isTop = kw.weight >= 80;
                  const isActive = selectedKeyword === kw.text;
                  return (
                    <button
                      key={idx}
                      onClick={() => setSelectedKeyword(isActive ? null : kw.text)}
                      className={`${style.size} whitespace-nowrap leading-tight cursor-pointer transition-all duration-200 hover:scale-110 hover:brightness-125 ${
                        isActive ? 'scale-110 brightness-125' : ''
                      }`}
                      style={{
                        color: isDark ? kw.color : adjustColorForLight(kw.color),
                        opacity: isActive ? 1 : style.opacity,
                        textShadow: (isTop || isActive) ? `0 0 12px ${kw.color}60` : 'none',
                        letterSpacing: kw.weight >= 85 ? '-0.01em' : 'normal',
                      }}
                    >
                      {kw.text}
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {selectedKeyword && selectedNews.length > 0 && (
            <div className={`relative z-10 border-t ${cardBorder} px-4 py-3 space-y-2 flex-shrink-0`}>
              <div className="flex items-center gap-2 mb-1">
                <span className="w-1.5 h-1.5 rounded-full bg-teal-500"></span>
                <span className={`text-xs font-bold ${accentTeal}`}>{selectedKeyword}</span>
                <span className={`${textMuted} text-xs`}>관련 뉴스 Top 2</span>
              </div>
              {selectedNews.map((article, idx) => (
                <a
                  key={idx}
                  href={article.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={`block rounded-lg p-3 transition-all cursor-pointer group ${newsBg} ${newsHover}`}
                >
                  <div className="flex items-start gap-2">
                    <span className={`w-5 h-5 rounded-full bg-teal-500/15 ${accentTeal} text-xs font-bold flex items-center justify-center flex-shrink-0 mt-0.5`}>
                      {idx + 1}
                    </span>
                    <div className="flex-1 min-w-0">
                      <p className={`text-xs leading-snug group-hover:${textMain} transition-colors line-clamp-2 ${textBody}`}>
                        {article.title}
                      </p>
                      <div className="flex items-center gap-1.5 mt-1.5">
                        <span className={`${textMuted} text-xs`}>{article.source || '—'}</span>
                        <span className={`${textDimmed} text-xs`}>·</span>
                        <span className={`${textMuted} text-xs`}>{article.date || '—'}</span>
                        <span className={`w-3.5 h-3.5 flex items-center justify-center ml-auto flex-shrink-0 ${textDimmed} group-hover:${accentTeal} transition-colors`}>
                          <i className="ri-external-link-line text-xs"></i>
                        </span>
                      </div>
                    </div>
                  </div>
                </a>
              ))}
            </div>
          )}

          {selectedKeyword && selectedNews.length === 0 && (
            <div className={`relative z-10 border-t ${cardBorder} px-4 py-2.5 flex-shrink-0`}>
              <div className="flex items-center gap-2">
                <span className="w-1.5 h-1.5 rounded-full bg-teal-500"></span>
                <span className={`text-xs font-bold ${accentTeal}`}>{selectedKeyword}</span>
                <p className={`${textMuted} text-xs`}>관련 뉴스 없음</p>
              </div>
            </div>
          )}

          {!selectedKeyword && (
            <div className={`relative z-10 border-t ${cardBorder} px-4 py-2.5 flex-shrink-0`}>
              <div className="flex items-center gap-2">
                <span className={`w-4 h-4 flex items-center justify-center ${textDimmed}`}>
                  <i className="ri-information-line text-xs"></i>
                </span>
                <p className={`${textMuted} text-xs`}>키워드를 클릭하면 관련 뉴스를 확인할 수 있습니다</p>
              </div>
            </div>
          )}
        </div>

        {/* ── 오른쪽: 브랜드 트래픽 Top 10 ── */}
        <div className="col-span-3 flex flex-col">
          <div className={`flex items-center gap-2 px-5 py-3 border-b ${cardBorder}`}>
            <span className={`w-4 h-4 flex items-center justify-center ${accentAmber}`}>
              <i className="ri-fire-line text-xs"></i>
            </span>
            <p className={`${textSub} text-xs font-semibold`}>브랜드 언급 Top 10</p>
            <span className={`${textDimmed} text-xs`}>Naver 뉴스 건수 기준</span>
          </div>

          <div className={`grid grid-cols-2 divide-x ${cardBorder}`}>
            <div className="py-1">
              {mediaLoading && (
                <div className={`px-4 py-6 text-center ${textMuted} text-xs`}>
                  <i className={`ri-loader-4-line animate-spin ${accentTeal} mr-2`}></i>로딩 중...
                </div>
              )}
              {!mediaLoading && mediaError && (
                <div className="px-4 py-6 text-center text-red-500 text-xs">미디어 인텔리전스 조회 실패 — {mediaError}</div>
              )}
              {!mediaLoading && !mediaError && brandTrafficData.length === 0 && (
                <div className={`px-4 py-6 text-center ${textMuted} text-xs`}>수집된 브랜드 데이터 없음</div>
              )}
              {brandTrafficData.map((brand) => {
                const heatColor = getHeatColor(brand.trafficIndex, maxTraffic);
                const isSelected = selectedBrand?.rank === brand.rank;
                const isUp = brand.change >= 0;
                return (
                  <button
                    key={brand.rank}
                    onClick={() => handleSelectBrand(brand)}
                    className={`w-full flex items-center gap-2 px-3 py-1.5 transition-all cursor-pointer text-left group ${isSelected ? selectedBg : hoverBg}`}
                  >
                    <span
                      className="text-xs font-black w-4 flex-shrink-0 text-center"
                      style={{ color: brand.rank <= 3 ? '#D97706' : isDark ? '#4A5568' : '#9CA3AF' }}
                    >
                      {brand.rank}
                    </span>
                    <span
                      className="w-1.5 h-1.5 rounded-full flex-shrink-0"
                      style={{
                        backgroundColor: brand.color,
                        boxShadow: isSelected ? `0 0 6px ${brand.color}80` : 'none',
                      }}
                    ></span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1 mb-0.5">
                        <span className={`text-xs font-bold truncate transition-colors ${isSelected ? textMain : textBody} group-hover:${textMain}`}>
                          {brand.brand}
                        </span>
                        {brand.rank <= 3 && (
                          <i className={`ri-fire-fill ${accentAmber} text-xs flex-shrink-0`}></i>
                        )}
                      </div>
                      <div className="flex items-center gap-1">
                        <div className={`flex-1 h-0.5 rounded-full overflow-hidden ${barTrack}`}>
                          <div
                            className="h-full rounded-full transition-all duration-700"
                            style={{ width: `${Math.round((brand.trafficIndex / maxTraffic) * 100)}%`, backgroundColor: heatColor }}
                          ></div>
                        </div>
                        <span className={`text-[10px] font-bold flex-shrink-0 tabular-nums ${getHeatClass(brand.trafficIndex, maxTraffic)}`}>
                          {brand.trafficIndex.toLocaleString()}
                        </span>
                      </div>
                    </div>
                    <div className={`flex items-center gap-0.5 px-2 py-0.5 rounded-full flex-shrink-0 border ${isUp ? upBadge : downBadge}`}>
                      <i className={`text-xs ${isUp ? 'ri-arrow-up-s-fill text-emerald-500' : 'ri-arrow-down-s-fill text-red-500'}`}></i>
                      <span className={`text-[11px] font-bold tabular-nums whitespace-nowrap ${isUp ? 'text-emerald-500' : 'text-red-500'}`}>
                        {Math.abs(brand.change)}%
                      </span>
                    </div>
                    <span className={`w-3 h-3 flex items-center justify-center flex-shrink-0 transition-all duration-200 ${isSelected ? accentTeal + ' rotate-90' : textDimmed + ' group-hover:' + textSub}`}>
                      <i className="ri-arrow-right-s-line text-sm"></i>
                    </span>
                  </button>
                );
              })}
            </div>

            <div className="flex flex-col min-h-0">
              {selectedBrand ? (
                <div className="flex flex-col h-full">
                  <div className={`flex items-center gap-2 px-4 py-2.5 border-b ${cardBorder} ${sectionBg}`}>
                    <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: selectedBrand.color, boxShadow: `0 0 6px ${selectedBrand.color}80` }}></span>
                    <span className={`text-xs font-bold ${textMain}`}>{selectedBrand.brand}</span>
                    <span className={`${textMuted} text-xs`}>·</span>
                    <span className={`${textSub} text-xs`}>{selectedBrand.company}</span>
                    <span className={`${textMuted} text-xs`}>·</span>
                    <span className={`${textMuted} text-xs`}>{selectedBrand.category}</span>
                    <button
                      onClick={() => { setSelectedBrand(null); setDrawerNews([]); }}
                      className={`ml-auto w-5 h-5 flex items-center justify-center ${textMuted} hover:${textMain} cursor-pointer transition-colors rounded`}
                    >
                      <i className="ri-close-line text-sm"></i>
                    </button>
                  </div>

                  <div className={`flex items-center gap-3 px-4 py-2.5 border-b ${cardBorder} bg-gray-50/30`}>
                    <div className="flex items-center gap-1.5">
                      <i className={`ri-bar-chart-2-line ${accentAmber} text-xs`}></i>
                      <span className={`${textSub} text-xs`}>뉴스 건수</span>
                      <span className={`text-xs font-bold tabular-nums ${getHeatClass(selectedBrand.trafficIndex, maxTraffic)}`}>
                        {selectedBrand.trafficIndex.toLocaleString()}
                      </span>
                    </div>
                    <span className={isDark ? 'text-[#1E2530]' : 'text-gray-300'}>|</span>
                    <div className={`flex items-center gap-1 px-2.5 py-1 rounded-full border ${selectedBrand.change >= 0 ? upBadge : downBadge}`}>
                      <i className={`text-sm font-black ${selectedBrand.change >= 0 ? 'ri-arrow-up-s-fill text-emerald-500' : 'ri-arrow-down-s-fill text-red-500'}`}></i>
                      <span className={`text-sm font-black tabular-nums ${selectedBrand.change >= 0 ? 'text-emerald-500' : 'text-red-500'}`}>
                        {Math.abs(selectedBrand.change)}%
                      </span>
                    </div>
                    <span className={`${textMuted} text-xs`}>전반기 대비</span>
                  </div>

                  <div className="flex-1 overflow-y-auto">
                    {drawerLoading && drawerNews.length === 0 && (
                      <div className={`px-4 py-6 text-center ${textMuted} text-xs`}>뉴스 로딩 중...</div>
                    )}
                    {!drawerLoading && drawerNews.length === 0 && (
                      <div className={`px-4 py-6 text-center ${textMuted} text-xs`}>관련 뉴스 없음</div>
                    )}
                    {drawerNews.map((article, idx) => (
                      <a
                        key={idx}
                        href={article.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className={`flex items-start gap-2.5 px-4 py-3 border-b ${cardBorder} last:border-0 ${hoverBg} transition-colors cursor-pointer group`}
                      >
                        <span className={`text-xs px-1.5 py-0.5 rounded font-semibold flex-shrink-0 mt-0.5 ${TAG_COLORS[article.tag] || (isDark ? 'bg-[#4A5568]/20 text-[#8B9BB4]' : 'bg-gray-100 text-gray-600')}`}>
                          {article.tag}
                        </span>
                        <div className="flex-1 min-w-0">
                          <p className={`text-xs leading-snug group-hover:${textMain} transition-colors line-clamp-2 mb-1 ${textBody}`}>
                            {article.title}
                          </p>
                          <div className="flex items-center gap-1.5">
                            <span className={`${textMuted} text-xs`}>{article.source || '—'}</span>
                            <span className={`${textDimmed} text-xs`}>·</span>
                            <span className={`${textMuted} text-xs`}>{article.date || '—'}</span>
                          </div>
                        </div>
                        <span className={`w-4 h-4 flex items-center justify-center flex-shrink-0 mt-0.5 ${textDimmed} group-hover:${accentTeal} transition-colors`}>
                          <i className="ri-external-link-line text-xs"></i>
                        </span>
                      </a>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center h-full py-8 px-4 text-center">
                  <div className={`w-10 h-10 rounded-xl flex items-center justify-center mb-3 ${emptyIconBg}`}>
                    <i className={`ri-newspaper-line text-xl ${emptyIconText}`}></i>
                  </div>
                  <p className={`${textMuted} text-xs leading-relaxed`}>
                    브랜드를 클릭하면<br />관련 뉴스 원문을 확인할 수 있습니다
                  </p>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function adjustColorForLight(hex: string): string {
  return hex;
}
