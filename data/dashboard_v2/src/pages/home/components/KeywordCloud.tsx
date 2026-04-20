import { useEffect, useMemo, useState } from 'react';
import {
  fetchMediaIntelligence,
  fetchBrandNews,
  type BrandTrafficEntry,
  type BrandNewsItem,
} from '@/api/mediaIntelligence';
import { listKeywords, type Keyword } from '@/api/keywordCloud';
import {
  fetchGovernmentSummary,
  type GovernmentSummaryResponse,
} from '@/api/governmentSummary';

// 브랜드 메타데이터 (Naver News 는 브랜드명만 제공 — 회사/계열/색상은 로컬 매핑)
const BRAND_META: Record<string, { company: string; category: string; color: string }> = {
  '키트루다':   { company: 'MSD',       category: 'Anti-PD-1',      color: '#00E5CC' },
  '렌비마':     { company: 'MSD/Eisai', category: 'VEGF TKI',       color: '#F59E0B' },
  '자누비아':   { company: 'MSD',       category: 'DPP-4',          color: '#60A5FA' },
  '가다실':     { company: 'MSD',       category: 'HPV Vaccine',    color: '#A78BFA' },
  '프로리아':   { company: 'Amgen',     category: 'RANKL',          color: '#F472B6' },
  '옵디보':     { company: 'BMS',       category: 'Anti-PD-1',      color: '#FB7185' },
  '타그리소':   { company: 'AstraZeneca', category: 'EGFR TKI',     color: '#34D399' },
  '임핀지':     { company: 'AstraZeneca', category: 'Anti-PD-L1',   color: '#22D3EE' },
  '테쎈트릭':   { company: 'Roche',     category: 'Anti-PD-L1',     color: '#C084FC' },
  '레블리미드': { company: 'BMS',       category: 'IMiD',           color: '#FACC15' },
  '다잘렉스':   { company: 'Janssen',   category: 'Anti-CD38',      color: '#FDBA74' },
  '린파자':     { company: 'AstraZeneca', category: 'PARP',         color: '#A3E635' },
};

const TAG_COLORS: Record<string, string> = {
  '급여': 'bg-emerald-400/10 text-emerald-400 border border-emerald-400/20',
  '약가': 'bg-red-400/10 text-red-400 border border-red-400/20',
  '허가': 'bg-amber-400/10 text-amber-400 border border-amber-400/20',
  '임상': 'bg-violet-400/10 text-violet-400 border border-violet-400/20',
  '시장': 'bg-[#00E5CC]/10 text-[#00E5CC] border border-[#00E5CC]/20',
  '뉴스': 'bg-sky-400/10 text-sky-400 border border-sky-400/20',
};

// 매체 분류 — source 필드(도메인 첫 토큰) 기준 매핑.
// Naver News API link 에서 _extract_source 로 추출된 값 (예: "chosun", "medigatenews", "weekly").
type MediaCategory = '일간지' | '인터넷신문' | '전문지/기타' | '매거진';

const MEDIA_MAP: Record<MediaCategory, string[]> = {
  '일간지': [
    'chosun', 'joongang', 'joins', 'donga', 'hankookilbo', 'hani', 'khan',
    'kmib', 'seoul', 'munhwa', 'segye', 'asiae', 'hankyung', 'mk', 'mt',
    'sedaily', 'heraldcorp', 'fnnews', 'edaily',
  ],
  '인터넷신문': [
    'yna', 'yonhapnews', 'news1', 'newsis', 'nocutnews', 'ohmynews', 'pressian',
    'mediatoday', 'ytn', 'kbs', 'mbc', 'sbs', 'jtbc', 'mbn', 'tvchosun',
    'dailian', 'newspim', 'biz', 'zdnet', 'mt', 'chosunbiz',
  ],
  '전문지/기타': [
    'medigatenews', 'medipharmhealth', 'medipana', 'doctorsnews', 'docdocdoc',
    'bosa', 'kmpnews', 'pharmnews', 'pharmstoday', 'dailypharm', 'yakup',
    'kpanews', 'rapportian', 'healthkorea', 'healthcaren', 'hkn24',
    'whosaeng', 'mediapharm', 'monews', 'medicaltimes', 'hitnews',
    'docdoc', 'mdtoday', 'medical-tribune', 'medical',
  ],
  '매거진': [
    'weekly', 'sisajournal', 'shindonga', 'magazine', 'economist', 'economychosun',
    'monthly', 'forbes', 'harpersbazaar', 'vogue',
  ],
};

const categorizeSource = (source: string | undefined): MediaCategory | null => {
  if (!source) return null;
  const s = source.toLowerCase();
  for (const cat of Object.keys(MEDIA_MAP) as MediaCategory[]) {
    if (MEDIA_MAP[cat].some((k) => s === k || s.includes(k))) return cat;
  }
  return null;
};

const MEDIA_CATEGORIES: readonly MediaCategory[] = ['일간지', '인터넷신문', '전문지/기타', '매거진'] as const;

const getHeatColor = (v: number, max: number): string => {
  const ratio = max ? v / max : 0;
  if (ratio >= 0.9) return '#FF3B3B';
  if (ratio >= 0.7) return '#FF7A00';
  if (ratio >= 0.5) return '#F59E0B';
  if (ratio >= 0.3) return '#00E5CC';
  return '#6B7280';
};

const getHeatClass = (v: number, max: number): string => {
  const ratio = max ? v / max : 0;
  if (ratio >= 0.9) return 'text-[#FF3B3B]';
  if (ratio >= 0.7) return 'text-[#FF7A00]';
  if (ratio >= 0.5) return 'text-[#F59E0B]';
  if (ratio >= 0.3) return 'text-[#00E5CC]';
  return 'text-[#6B7280]';
};

// 최근 N일을 7주 시리즈로 다운샘플 (스파크라인 표시용)
const bucketWeekly = (daily: number[], buckets = 7): number[] => {
  if (!daily.length) return [];
  const out: number[] = [];
  const size = Math.ceil(daily.length / buckets);
  for (let i = 0; i < buckets; i++) {
    const seg = daily.slice(i * size, (i + 1) * size);
    out.push(seg.reduce((a, b) => a + b, 0));
  }
  return out;
};

const computeChange = (daily: number[]): number => {
  if (!daily.length) return 0;
  const half = Math.floor(daily.length / 2);
  const prev = daily.slice(0, half).reduce((a, b) => a + b, 0);
  const curr = daily.slice(half).reduce((a, b) => a + b, 0);
  if (!prev) return curr ? 100 : 0;
  return Math.round(((curr - prev) / prev) * 100);
};

// 제목/설명으로 태그 추론
const inferTag = (n: BrandNewsItem): string => {
  const text = `${n.title} ${n.description}`;
  if (/급여|보험|등재|상한/.test(text)) return '급여';
  if (/약가|가격|인하|인상/.test(text)) return '약가';
  if (/승인|허가|적응증|FDA|EMA|식약처/.test(text)) return '허가';
  if (/임상|3상|2상|phase|trial/i.test(text)) return '임상';
  if (/매출|시장|점유|성장/.test(text)) return '시장';
  return '뉴스';
};

interface BrandDisplay {
  rank: number;
  brand: string;
  company: string;
  category: string;
  color: string;
  total: number;
  change: number;
  weeklySparkline: number[];
  dailySparkline: number[];
  latestNews: BrandNewsItem[];
}

// ── Sparkline SVG 미니 차트 컴포넌트 ──────────────────────────────────────────
function Sparkline({ data, color, width = 52, height = 20, isSelected = false }: {
  data: number[]; color: string; width?: number; height?: number; isSelected?: boolean;
}) {
  if (!data || data.length < 2) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const padX = 1;
  const padY = 2;
  const innerW = width - padX * 2;
  const innerH = height - padY * 2;

  const points = data.map((v, i) => {
    const x = padX + (i / (data.length - 1)) * innerW;
    const y = padY + innerH - ((v - min) / range) * innerH;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });

  const polyline = points.join(' ');
  const lastPt = points[points.length - 1].split(',');
  const lastX = parseFloat(lastPt[0]);
  const lastY = parseFloat(lastPt[1]);
  const firstPt = points[0].split(',');
  const firstX = parseFloat(firstPt[0]);
  const bottomY = padY + innerH;
  const areaPoints = `${firstX},${bottomY} ${polyline} ${lastX},${bottomY}`;

  const isUp = data[data.length - 1] >= data[0];
  const lineColor = isSelected ? color : isUp ? color : '#EF4444';
  const areaOpacity = isSelected ? 0.18 : 0.08;

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="flex-shrink-0" style={{ overflow: 'visible' }}>
      <polygon points={areaPoints} fill={lineColor} opacity={areaOpacity} />
      <polyline points={polyline} fill="none" stroke={lineColor} strokeWidth={isSelected ? 1.8 : 1.4} strokeLinejoin="round" strokeLinecap="round" opacity={isSelected ? 1 : 0.85} />
      <circle cx={lastX} cy={lastY} r={isSelected ? 2.2 : 1.8} fill={lineColor} opacity={isSelected ? 1 : 0.9} />
      {isSelected && <circle cx={lastX} cy={lastY} r={4} fill={lineColor} opacity={0.2} />}
    </svg>
  );
}

export default function KeywordCloud() {
  const [selectedBrand, setSelectedBrand] = useState<BrandDisplay | null>(null);
  const [rawEntries, setRawEntries] = useState<BrandTrafficEntry[]>([]);
  const [brandLoading, setBrandLoading] = useState(true);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [keywordCloudData, setKeywordCloudData] = useState<Keyword[]>([]);
  const [drawerNews, setDrawerNews] = useState<BrandNewsItem[]>([]);
  const [drawerLoading, setDrawerLoading] = useState(false);
  const [govSummary, setGovSummary] = useState<GovernmentSummaryResponse | null>(null);
  const [govLoading, setGovLoading] = useState(true);
  const [mediaFilter, setMediaFilter] = useState<MediaCategory | 'all'>('all');

  useEffect(() => {
    fetchMediaIntelligence()  // 서버에서 달력 기반 '지난 1개월' 자동 계산
      .then(res => {
        setRawEntries(res.brands || []);
        setUpdatedAt(res.updated_at);
        if (res.error) setLoadError(res.error);
      })
      .catch(e => setLoadError(e.message || '미디어 인텔리전스 로딩 실패'))
      .finally(() => setBrandLoading(false));
    listKeywords()
      .then(setKeywordCloudData)
      .catch(() => setKeywordCloudData([]));
    fetchGovernmentSummary()
      .then(setGovSummary)
      .catch(() => setGovSummary(null))
      .finally(() => setGovLoading(false));
  }, []);

  const brands = useMemo<BrandDisplay[]>(() => {
    return rawEntries.slice(0, 10).map((b, i) => {
      const meta = BRAND_META[b.brand] ?? { company: '—', category: '—', color: '#8B9BB4' };
      return {
        rank: i + 1,
        brand: b.brand,
        company: meta.company,
        category: meta.category,
        color: meta.color,
        total: b.total_count,
        change: computeChange(b.sparkline),
        weeklySparkline: bucketWeekly(b.sparkline, 7),
        dailySparkline: b.sparkline,
        latestNews: b.latest_news,
      };
    });
  }, [rawEntries]);

  const handleSelect = async (brand: BrandDisplay) => {
    if (selectedBrand?.brand === brand.brand) {
      setSelectedBrand(null);
      setDrawerNews([]);
      return;
    }
    setSelectedBrand(brand);
    setMediaFilter('all');
    setDrawerNews(brand.latestNews);  // 기본 5건 즉시 노출
    setDrawerLoading(true);
    try {
      const res = await fetchBrandNews(brand.brand, 10);
      setDrawerNews(res.items);
    } catch {
      // fallback to initial 5
    } finally {
      setDrawerLoading(false);
    }
  };

  const maxWeight = keywordCloudData.length > 0 ? Math.max(...keywordCloudData.map(k => k.weight)) : 1;
  const maxTraffic = brands.length > 0 ? Math.max(...brands.map(b => b.total)) : 1;

  const getCloudStyle = (weight: number) => {
    const ratio = weight / maxWeight;
    if (ratio >= 0.9) return { size: 'text-[22px] font-black', opacity: 1.0 };
    if (ratio >= 0.78) return { size: 'text-lg font-bold', opacity: 0.95 };
    if (ratio >= 0.65) return { size: 'text-base font-bold', opacity: 0.88 };
    if (ratio >= 0.52) return { size: 'text-sm font-semibold', opacity: 0.78 };
    if (ratio >= 0.42) return { size: 'text-[13px] font-medium', opacity: 0.65 };
    return { size: 'text-xs font-normal', opacity: 0.5 };
  };

  const top1 = brands[0];

  return (
    <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-5 pt-4 pb-3 border-b border-[#1E2530]">
        <span className="w-5 h-5 flex items-center justify-center">
          <i className="ri-cloud-line text-[#00E5CC] text-base"></i>
        </span>
        <h3 className="text-white font-bold text-sm">미디어 인텔리전스</h3>
        {top1 && (
          <div className="flex items-center gap-1.5 ml-3 bg-[#FF3B3B]/10 border border-[#FF3B3B]/20 rounded-full px-2.5 py-0.5">
            <span className="w-3 h-3 flex items-center justify-center text-[#FF3B3B]"><i className="ri-fire-fill text-xs"></i></span>
            <span className="text-[#FF3B3B] text-xs font-semibold">HOT</span>
            <span className="text-[#FF3B3B] text-xs font-bold">{top1.brand}</span>
            <span className="text-[#FF7A00]/70 text-xs">{top1.company}</span>
          </div>
        )}
        <span className="ml-auto text-[#4A5568] text-xs">
          {loadError ? <span className="text-red-400">{loadError}</span> : `지난 1개월 · Naver News${updatedAt ? ` · ${updatedAt.slice(0, 10)}` : ''}`}
        </span>
      </div>

      <div className="grid grid-cols-5">
        {/* ── 왼쪽: 정부 키워드 클라우드 ── */}
        <div className="col-span-2 px-5 py-5 border-r border-[#1E2530] relative overflow-hidden">
          <div className="absolute inset-0 pointer-events-none">
            <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-48 h-48 rounded-full bg-[#00E5CC]/3 blur-3xl"></div>
          </div>

          <div className="flex items-center gap-2 mb-4 relative z-10">
            <span className="w-4 h-4 flex items-center justify-center text-[#8B9BB4]">
              <i className="ri-government-line text-xs"></i>
            </span>
            <p className="text-[#8B9BB4] text-xs font-semibold">정부 기관 키워드</p>
            <span className="text-[#2A3545] text-xs">보건복지부 · 건보공단 · 심평원</span>
          </div>

          <div className="relative z-10 flex flex-wrap gap-x-3 gap-y-2 items-center justify-center min-h-[120px] content-center px-2">
            {keywordCloudData.map((kw) => {
              const style = getCloudStyle(kw.weight);
              const isTop = kw.weight >= 80;
              const kwColor = kw.color || '#8B9BB4';
              return (
                <span
                  key={kw.id}
                  className={`${style.size} whitespace-nowrap leading-tight cursor-default select-none transition-all duration-200 hover:scale-110 hover:brightness-125`}
                  style={{
                    color: kwColor,
                    opacity: style.opacity,
                    textShadow: isTop ? `0 0 12px ${kwColor}60` : 'none',
                    letterSpacing: kw.weight >= 85 ? '-0.01em' : 'normal',
                  }}
                >
                  {kw.text}
                </span>
              );
            })}
          </div>

          {/* ── 정부 키워드 AI 요약 (리뷰어 다수결) ───────────────────────────── */}
          <div className="relative z-10 mt-4 pt-4 border-t border-[#1E2530]">
            <div className="flex items-center gap-2 mb-2">
              <span className="w-4 h-4 flex items-center justify-center text-[#00E5CC]">
                <i className="ri-sparkling-2-line text-xs"></i>
              </span>
              <p className="text-[#C9D1D9] text-xs font-semibold">AI 정책 동향 요약</p>
              <span className="text-[#2A3545] text-[10px]">지난 1개월</span>
              {govSummary?.reviewers && govSummary.reviewers.length > 0 && (
                <span className="ml-auto flex items-center gap-1 text-[#4A5568] text-[10px]">
                  <i className="ri-shield-check-line text-[10px]"></i>
                  리뷰어 {govSummary.reviewers.length}인
                </span>
              )}
            </div>
            {govLoading && (
              <p className="text-[#4A5568] text-xs">요약 생성 중…</p>
            )}
            {!govLoading && govSummary?.markdown && (
              <div className="text-[#C9D1D9] text-xs leading-relaxed whitespace-pre-wrap font-normal">
                {govSummary.markdown}
              </div>
            )}
            {!govLoading && govSummary?.sources && govSummary.sources.length > 0 && (
              <div className="mt-3 pt-3 border-t border-[#1E2530]/60">
                <p className="text-[#4A5568] text-[10px] font-semibold uppercase tracking-wider mb-1.5">
                  참고 자료 · {govSummary.sources.length}건
                </p>
                <ul className="space-y-1">
                  {govSummary.sources.slice(0, 6).map((s, i) => (
                    <li key={`${s.url}-${i}`} className="flex items-start gap-1.5">
                      <i className="ri-external-link-line text-[10px] text-[#4A5568] mt-0.5 flex-shrink-0"></i>
                      <a
                        href={s.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-[#8B9BB4] hover:text-[#00E5CC] text-[11px] leading-snug transition-colors line-clamp-2 flex-1"
                      >
                        {s.title}
                      </a>
                      <span className="text-[#4A5568] text-[10px] whitespace-nowrap flex-shrink-0">
                        {s.source}{s.date && ` · ${s.date}`}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {!govLoading && !govSummary?.markdown && (
              <p className="text-[#4A5568] text-xs italic">
                {govSummary?.error || '요약 데이터 없음 — OpenAI/Gemini 키 확인'}
              </p>
            )}
          </div>
        </div>

        {/* ── 오른쪽: 브랜드 트래픽 Top 10 ── */}
        <div className="col-span-3 flex flex-col">
          <div className="flex items-center gap-2 px-5 py-3 border-b border-[#1E2530]">
            <span className="w-4 h-4 flex items-center justify-center text-[#F59E0B]">
              <i className="ri-fire-line text-xs"></i>
            </span>
            <p className="text-[#8B9BB4] text-xs font-semibold">브랜드 언급 Top {brands.length}</p>
            <span className="text-[#2A3545] text-xs">Naver 뉴스 건수 기준</span>
            <span className="ml-auto flex items-center gap-1 text-[#4A5568] text-xs">
              <i className="ri-pulse-line text-xs"></i>
              1개월 추이
            </span>
          </div>

          <div className="grid grid-cols-2 divide-x divide-[#1E2530]">
            {/* Brand List */}
            <div className="py-1">
              {brandLoading && (
                <div className="px-4 py-6 text-center text-[#4A5568] text-xs">로딩 중…</div>
              )}
              {!brandLoading && brands.length === 0 && !loadError && (
                <div className="px-4 py-6 text-center text-[#4A5568] text-xs">수집된 데이터 없음</div>
              )}
              {brands.map((brand) => {
                const heatColor = getHeatColor(brand.total, maxTraffic);
                const isSelected = selectedBrand?.brand === brand.brand;
                return (
                  <button
                    key={brand.brand}
                    onClick={() => handleSelect(brand)}
                    className={`w-full flex items-center gap-2 px-3 py-1.5 transition-all cursor-pointer text-left group ${
                      isSelected ? 'bg-[#1E2530]' : 'hover:bg-[#1E2530]/50'
                    }`}
                  >
                    <span className="text-xs font-black w-4 flex-shrink-0 text-center" style={{ color: brand.rank <= 3 ? '#F59E0B' : '#4A5568' }}>
                      {brand.rank}
                    </span>
                    <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ backgroundColor: brand.color, boxShadow: isSelected ? `0 0 6px ${brand.color}80` : 'none' }}></span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1 mb-0.5">
                        <span className={`text-xs font-bold truncate transition-colors ${isSelected ? 'text-white' : 'text-[#C9D1D9] group-hover:text-white'}`}>
                          {brand.brand}
                        </span>
                        {brand.rank <= 3 && <i className="ri-fire-fill text-[#F59E0B] text-xs flex-shrink-0"></i>}
                      </div>
                      <div className="flex items-center gap-1">
                        <div className="flex-1 h-0.5 bg-[#0D1117] rounded-full overflow-hidden">
                          <div className="h-full rounded-full transition-all duration-700" style={{ width: `${Math.round((brand.total / maxTraffic) * 100)}%`, backgroundColor: heatColor }}></div>
                        </div>
                        <span className={`text-[10px] font-bold flex-shrink-0 tabular-nums ${getHeatClass(brand.total, maxTraffic)}`}>
                          {brand.total.toLocaleString()}
                        </span>
                      </div>
                    </div>
                    <div className="flex-shrink-0 flex items-center">
                      <Sparkline data={brand.dailySparkline} color={brand.color} width={52} height={20} isSelected={isSelected} />
                    </div>
                    <span className={`text-[10px] font-semibold flex-shrink-0 whitespace-nowrap ${brand.change >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {brand.change >= 0 ? '+' : ''}{brand.change}%
                    </span>
                    <span className={`w-3 h-3 flex items-center justify-center flex-shrink-0 transition-all duration-200 ${isSelected ? 'text-[#00E5CC] rotate-90' : 'text-[#2A3545] group-hover:text-[#8B9BB4]'}`}>
                      <i className="ri-arrow-right-s-line text-sm"></i>
                    </span>
                  </button>
                );
              })}
            </div>

            {/* News Panel */}
            <div className="flex flex-col min-h-0">
              {selectedBrand ? (
                <div className="flex flex-col h-full">
                  <div className="flex items-center gap-2 px-4 py-2.5 border-b border-[#1E2530] bg-[#0D1117]/30">
                    <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: selectedBrand.color, boxShadow: `0 0 6px ${selectedBrand.color}80` }}></span>
                    <span className="text-white text-xs font-bold">{selectedBrand.brand}</span>
                    <span className="text-[#4A5568] text-xs">·</span>
                    <span className="text-[#8B9BB4] text-xs">{selectedBrand.company}</span>
                    <span className="text-[#4A5568] text-xs">·</span>
                    <span className="text-[#4A5568] text-xs">{selectedBrand.category}</span>
                    <button onClick={() => { setSelectedBrand(null); setDrawerNews([]); }} className="ml-auto w-5 h-5 flex items-center justify-center text-[#4A5568] hover:text-white cursor-pointer transition-colors rounded">
                      <i className="ri-close-line text-sm"></i>
                    </button>
                  </div>

                  <div className="flex items-center gap-3 px-4 py-2.5 border-b border-[#1E2530] bg-[#0D1117]/20">
                    <div className="flex items-center gap-1.5">
                      <i className="ri-bar-chart-2-line text-[#F59E0B] text-xs"></i>
                      <span className="text-[#8B9BB4] text-xs">건수</span>
                      <span className={`text-xs font-bold tabular-nums ${getHeatClass(selectedBrand.total, maxTraffic)}`}>
                        {selectedBrand.total.toLocaleString()}
                      </span>
                    </div>
                    <span className="text-[#1E2530]">|</span>
                    <span className={`text-xs font-semibold ${selectedBrand.change >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {selectedBrand.change >= 0 ? '▲' : '▼'} {Math.abs(selectedBrand.change)}%
                    </span>
                    <span className="text-[#4A5568] text-xs">전반기 대비</span>
                    <div className="ml-auto flex items-center gap-2">
                      <span className="text-[#4A5568] text-[10px]">1개월 추이</span>
                      <Sparkline data={selectedBrand.dailySparkline} color={selectedBrand.color} width={72} height={24} isSelected />
                    </div>
                  </div>

                  <div className="flex items-center gap-1 px-4 py-1.5 border-b border-[#1E2530]/50 bg-[#0D1117]/10">
                    {selectedBrand.weeklySparkline.map((val, idx) => {
                      const isLast = idx === selectedBrand.weeklySparkline.length - 1;
                      const weekLabel = idx === 0 ? '-6W' : idx === 1 ? '-5W' : idx === 2 ? '-4W' : idx === 3 ? '-3W' : idx === 4 ? '-2W' : idx === 5 ? '-1W' : 'NOW';
                      return (
                        <div key={idx} className={`flex-1 text-center ${isLast ? 'opacity-100' : 'opacity-60'}`}>
                          <div className={`text-[9px] font-medium ${isLast ? 'text-[#8B9BB4]' : 'text-[#4A5568]'}`}>{weekLabel}</div>
                          <div className="text-[9px] font-bold tabular-nums" style={{ color: isLast ? selectedBrand.color : '#4A5568' }}>
                            {val}
                          </div>
                        </div>
                      );
                    })}
                  </div>

                  {/* ── 매체 카테고리 필터 ─────────────────────────────── */}
                  <div className="flex items-center gap-1.5 px-4 py-2 border-b border-[#1E2530]/50 bg-[#0D1117]/30 overflow-x-auto">
                    {(['all', ...MEDIA_CATEGORIES] as const).map((cat) => {
                      const label = cat === 'all' ? '전체' : cat;
                      const count = cat === 'all'
                        ? drawerNews.length
                        : drawerNews.filter((n) => categorizeSource(n.source) === cat).length;
                      const isActive = mediaFilter === cat;
                      return (
                        <button
                          key={cat}
                          onClick={() => setMediaFilter(cat)}
                          className={`flex-shrink-0 text-[10px] px-2 py-0.5 rounded-full font-semibold transition-all ${
                            isActive
                              ? 'bg-[#00E5CC] text-[#0A0E1A]'
                              : 'bg-[#1E2530] text-[#8B9BB4] hover:bg-[#2A3545] hover:text-white'
                          }`}
                        >
                          {label} <span className="opacity-70">({count})</span>
                        </button>
                      );
                    })}
                  </div>

                  <div className="flex-1 overflow-y-auto">
                    {drawerLoading && drawerNews.length === 0 && (
                      <div className="px-4 py-6 text-center text-[#4A5568] text-xs">뉴스 로딩 중…</div>
                    )}
                    {drawerNews
                      .filter((n) => mediaFilter === 'all' || categorizeSource(n.source) === mediaFilter)
                      .map((article, idx) => {
                      const tag = inferTag(article);
                      return (
                        <a
                          key={idx}
                          href={article.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex items-start gap-2.5 px-4 py-3 border-b border-[#1E2530] last:border-0 hover:bg-[#1E2530]/50 transition-colors cursor-pointer group"
                        >
                          <span className={`text-xs px-1.5 py-0.5 rounded font-semibold flex-shrink-0 mt-0.5 ${TAG_COLORS[tag] || 'bg-[#4A5568]/20 text-[#8B9BB4]'}`}>
                            {tag}
                          </span>
                          <div className="flex-1 min-w-0">
                            <p className="text-[#C9D1D9] text-xs leading-snug group-hover:text-white transition-colors line-clamp-2 mb-1">
                              {article.title}
                            </p>
                            <div className="flex items-center gap-1.5">
                              <span className="text-[#4A5568] text-xs">{article.source}</span>
                              <span className="text-[#2A3545] text-xs">·</span>
                              <span className="text-[#4A5568] text-xs">{article.date}</span>
                            </div>
                          </div>
                          <span className="w-4 h-4 flex items-center justify-center text-[#2A3545] group-hover:text-[#00E5CC] transition-colors flex-shrink-0 mt-0.5">
                            <i className="ri-external-link-line text-xs"></i>
                          </span>
                        </a>
                      );
                    })}
                    {!drawerLoading && drawerNews.length === 0 && (
                      <div className="px-4 py-6 text-center text-[#4A5568] text-xs">관련 뉴스 없음</div>
                    )}
                    {!drawerLoading && drawerNews.length > 0 &&
                      drawerNews.filter((n) => mediaFilter === 'all' || categorizeSource(n.source) === mediaFilter).length === 0 && (
                        <div className="px-4 py-6 text-center text-[#4A5568] text-xs">선택한 매체 카테고리 뉴스 없음</div>
                      )}
                  </div>
                </div>
              ) : (
                <div className="flex flex-col h-full">
                  <div className="px-4 py-3 border-b border-[#1E2530]">
                    <p className="text-[#4A5568] text-[10px] mb-2 font-medium">주간 버킷 미리보기</p>
                    <div className="space-y-1.5">
                      {brands.slice(0, 5).map((brand) => (
                        <div key={brand.brand} className="flex items-center gap-2">
                          <span className="text-[10px] text-[#4A5568] w-3 text-right">{brand.rank}</span>
                          <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ backgroundColor: brand.color }}></span>
                          <span className="text-[10px] text-[#6B7280] w-16 truncate">{brand.brand}</span>
                          <Sparkline data={brand.dailySparkline} color={brand.color} width={60} height={16} />
                          <span className="text-[10px] font-semibold ml-auto whitespace-nowrap" style={{ color: brand.change >= 0 ? '#34D399' : '#F87171' }}>
                            {brand.change >= 0 ? '+' : ''}{brand.change}%
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>

                  <div className="flex flex-col items-center justify-center flex-1 py-8 px-4 text-center">
                    <div className="w-10 h-10 rounded-xl bg-[#1E2530] flex items-center justify-center mb-3">
                      <i className="ri-newspaper-line text-[#2A3545] text-xl"></i>
                    </div>
                    <p className="text-[#4A5568] text-xs leading-relaxed">
                      브랜드를 클릭하면<br />관련 뉴스 원문을 확인할 수 있습니다
                    </p>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
