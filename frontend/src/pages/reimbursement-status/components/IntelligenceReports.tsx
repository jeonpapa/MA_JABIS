import { useState, useMemo } from 'react';
import { useApi } from '@/hooks/useApi';
import {
  fetchReports,
  openReportPdf,
  IntelligenceReport,
  REPORT_CATEGORY_LABELS,
} from '@/api/reimbStatus';

const CATEGORY_GROUPS: Record<string, { label: string; group: 'cancer' | 'evaluation' }> = {
  'pre-cancer': { label: REPORT_CATEGORY_LABELS['pre-cancer'], group: 'cancer' },
  'post-cancer': { label: REPORT_CATEGORY_LABELS['post-cancer'], group: 'cancer' },
  'pre-evaluation': { label: REPORT_CATEGORY_LABELS['pre-evaluation'], group: 'evaluation' },
  'post-evaluation': { label: REPORT_CATEGORY_LABELS['post-evaluation'], group: 'evaluation' },
  monthly: { label: REPORT_CATEGORY_LABELS.monthly, group: 'evaluation' },
  other: { label: REPORT_CATEGORY_LABELS.other, group: 'evaluation' },
};

const CATEGORY_ORDER = ['pre-cancer', 'post-cancer', 'pre-evaluation', 'post-evaluation', 'monthly', 'other'];

function CategoryBadge({ category, isDark }: { category: IntelligenceReport['category']; isDark: boolean }) {
  const group = CATEGORY_GROUPS[category]?.group ?? 'cancer';
  const isCancer = group === 'cancer';
  return (
    <span className={`text-[11px] font-bold px-2.5 py-1 rounded-full border whitespace-nowrap ${
      isCancer
        ? (isDark ? 'text-amber-300 bg-amber-400/10 border-amber-400/30' : 'text-amber-700 bg-amber-50 border-amber-200')
        : (isDark ? 'text-teal-300 bg-teal-400/10 border-teal-400/30' : 'text-teal-700 bg-teal-50 border-teal-200')
    }`}>
      {CATEGORY_GROUPS[category]?.label ?? category}
    </span>
  );
}

function ReportCard({ report, isDark }: { report: IntelligenceReport; isDark: boolean }) {
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const group = CATEGORY_GROUPS[report.category]?.group ?? 'cancer';
  const isCancer = group === 'cancer';

  const cardClasses = isDark
    ? (isCancer
      ? 'bg-[#1E1A10] border-[#F59E0B]/15 hover:border-[#F59E0B]/35'
      : 'bg-[#0F1A18] border-teal-400/15 hover:border-teal-400/35')
    : (isCancer
      ? 'bg-amber-50/60 border-amber-200 hover:border-amber-400 hover:shadow-sm'
      : 'bg-teal-50/60 border-teal-200 hover:border-teal-400 hover:shadow-sm');

  const accentLine = isCancer
    ? (isDark ? 'bg-[#F59E0B]/40' : 'bg-amber-300')
    : (isDark ? 'bg-teal-400/40' : 'bg-teal-300');

  const accentColor = isCancer
    ? (isDark ? 'text-[#F59E0B]' : 'text-amber-600')
    : (isDark ? 'text-teal-400' : 'text-teal-600');

  const accentBg = isCancer
    ? (isDark ? 'bg-[#F59E0B]/10 border-[#F59E0B]/25' : 'bg-amber-100/80 border-amber-200')
    : (isDark ? 'bg-teal-400/10 border-teal-400/25' : 'bg-teal-100/80 border-teal-200');

  const dotColor = isCancer ? (isDark ? 'bg-[#F59E0B]' : 'bg-amber-400') : (isDark ? 'bg-teal-400' : 'bg-teal-400');

  const textColor = isDark ? 'text-white' : 'text-gray-900';
  const mutedText = isDark ? 'text-[#8B9BB4]' : 'text-gray-600';
  const metaText = isDark ? 'text-[#6B7A90]' : 'text-gray-400';

  const handleDownload = async () => {
    if (downloading) return;
    setDownloading(true);
    setDownloadError(null);
    try {
      await openReportPdf(report.downloadId);
    } catch (e) {
      setDownloadError(e instanceof Error ? e.message : 'PDF 다운로드 실패');
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div
      onClick={handleDownload}
      className={`block border rounded-2xl p-5 transition-all cursor-pointer group ${cardClasses}`}
    >
      <div className={`h-0.5 -mx-5 -mt-5 mb-4 rounded-t-2xl ${accentLine}`} />

      <div className="flex items-start justify-between mb-3">
        <CategoryBadge category={report.category} isDark={isDark} />
        <div className={`flex items-center gap-1.5 text-[11px] font-medium ${metaText}`}>
          <span className="w-4 h-4 flex items-center justify-center">
            <i className="ri-file-list-line text-sm"></i>
          </span>
          {report.pages != null ? `${report.pages}p` : '—'}
        </div>
      </div>

      <h3 className={`text-base font-bold mb-3 leading-snug group-hover:underline ${textColor}`}>{report.title}</h3>

      <div className={`flex items-center gap-4 mb-3 ${metaText}`}>
        <div className="flex items-center gap-1.5 text-[11px]">
          <span className="w-4 h-4 flex items-center justify-center">
            <i className="ri-calendar-line text-sm"></i>
          </span>
          {report.date}
        </div>
        <div className="flex items-center gap-1.5 text-[11px]">
          <span className="w-4 h-4 flex items-center justify-center">
            <i className="ri-archive-line text-sm"></i>
          </span>
          {report.fileSize}
        </div>
      </div>

      <div className={`rounded-lg px-3 py-2.5 mb-3.5 border ${accentBg}`}>
        <p className={`text-xs font-semibold ${mutedText}`}>{report.cycle}</p>
      </div>

      <div className="space-y-2 mb-4">
        {report.highlights.map((h, i) => (
          <div key={i} className="flex items-start gap-2.5">
            <span className={`w-2 h-2 rounded-full flex-shrink-0 mt-1.5 ${dotColor}`} />
            <p className={`text-xs leading-relaxed ${mutedText}`}>{h}</p>
          </div>
        ))}
        {report.highlights.length === 0 && (
          <p className={`text-xs ${metaText}`}>하이라이트 정보 없음</p>
        )}
      </div>

      <div className="flex items-center justify-end gap-2">
        {downloadError && (
          <span className="text-[11px] text-red-400">{downloadError}</span>
        )}
        <span className={`flex items-center gap-1.5 border text-xs font-semibold px-3.5 py-2 rounded-lg transition-all whitespace-nowrap group-hover:scale-105 ${accentBg} ${accentColor}`}>
          <span className="w-4 h-4 flex items-center justify-center">
            <i className={downloading ? 'ri-loader-4-line animate-spin text-sm' : 'ri-download-line text-sm'}></i>
          </span>
          PDF
        </span>
      </div>
    </div>
  );
}

export default function IntelligenceReports({ isDark }: { isDark: boolean }) {
  const [filter, setFilter] = useState<string>('all');
  const [selectedYear, setSelectedYear] = useState<number | null>(null);
  const [searchQuery, setSearchQuery] = useState<string>('');

  const { data, loading, error, reload } = useApi(fetchReports);
  const intelligenceReports = useMemo(() => data ?? [], [data]);

  const availableYears = useMemo(() => {
    const years = [...new Set(intelligenceReports.map(r => r.year))];
    return years.sort((a, b) => b - a);
  }, [intelligenceReports]);

  const activeYear = selectedYear ?? availableYears[0] ?? new Date().getFullYear();

  const categories = [
    { key: 'all', label: '전체' },
    { key: 'pre-cancer', label: '암질심 전' },
    { key: 'post-cancer', label: '암질심 후' },
    { key: 'pre-evaluation', label: '약평위 전' },
    { key: 'post-evaluation', label: '약평위 후' },
  ];

  const displayedReports = useMemo(() => {
    let yearFiltered = intelligenceReports.filter(r => r.year === activeYear);

    if (searchQuery.trim()) {
      const query = searchQuery.trim().toLowerCase();
      yearFiltered = yearFiltered.filter(r =>
        r.title.toLowerCase().includes(query) ||
        r.cycle.toLowerCase().includes(query) ||
        r.summary.toLowerCase().includes(query) ||
        r.highlights.some(h => h.toLowerCase().includes(query))
      );
    }

    if (filter === 'all') {
      const latest: IntelligenceReport[] = [];
      for (const cat of CATEGORY_ORDER) {
        const catReports = yearFiltered
          .filter(r => r.category === cat)
          .sort((a, b) => b.date.localeCompare(a.date));
        if (catReports.length > 0) latest.push(catReports[0]);
      }
      return latest;
    }

    return yearFiltered
      .filter(r => r.category === filter)
      .sort((a, b) => b.date.localeCompare(a.date));
  }, [intelligenceReports, filter, activeYear, searchQuery]);

  const containerBg = isDark ? '' : 'bg-white border-gray-200';
  const filterBg = isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-gray-50 border-gray-200';
  const inputBg = isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-gray-50 border-gray-200';
  const activeTab = 'bg-teal-500 text-white';
  const inactiveTab = isDark ? 'text-[#8B9BB4] hover:text-white' : 'text-gray-500 hover:text-gray-900';

  return (
    <div className={`rounded-2xl border p-6 ${containerBg}`}>
      <div className="flex items-center justify-between mb-5 flex-wrap gap-3">
        <div className="flex items-center gap-2.5">
          <span className={`w-6 h-6 flex items-center justify-center ${isDark ? 'text-teal-400' : 'text-teal-600'}`}>
            <i className="ri-file-paper-2-line text-base"></i>
          </span>
          <div>
            <h2 className={`font-bold text-base ${isDark ? 'text-white' : 'text-gray-900'}`}>Intelligence Reports</h2>
            <p className={`text-xs mt-0.5 ${isDark ? 'text-[#8B9BB4]' : 'text-gray-500'}`}>암질심 및 약평위 회차별 사이클 분석 보고서</p>
          </div>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          <div className={`flex items-center gap-1.5 rounded-lg px-3 py-2 min-w-[200px] border ${inputBg}`}>
            <span className={`w-4 h-4 flex items-center justify-center flex-shrink-0 ${isDark ? 'text-[#6B7A90]' : 'text-gray-400'}`}>
              <i className="ri-search-line text-sm"></i>
            </span>
            <input
              type="text"
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              placeholder="약제명, 성분명 검색..."
              className={`bg-transparent text-xs outline-none flex-1 min-w-0 ${
                isDark ? 'text-white placeholder-[#6B7A90]' : 'text-gray-900 placeholder-gray-400'
              }`}
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery('')}
                className={`w-4 h-4 flex items-center justify-center cursor-pointer flex-shrink-0 transition-colors ${
                  isDark ? 'text-[#6B7A90] hover:text-white' : 'text-gray-400 hover:text-gray-600'
                }`}
              >
                <i className="ri-close-line text-sm"></i>
              </button>
            )}
          </div>

          <div className={`flex items-center gap-1.5 rounded-lg px-3 py-2 border ${filterBg}`}>
            <span className={`w-4 h-4 flex items-center justify-center ${isDark ? 'text-[#6B7A90]' : 'text-gray-400'}`}>
              <i className="ri-calendar-line text-sm"></i>
            </span>
            <select
              value={activeYear}
              onChange={e => setSelectedYear(Number(e.target.value))}
              className={`bg-transparent text-xs font-bold cursor-pointer outline-none appearance-none pr-1 ${
                isDark ? 'text-white' : 'text-gray-900'
              }`}
            >
              {(availableYears.length > 0 ? availableYears : [activeYear]).map(y => (
                <option key={y} value={y} className={isDark ? 'bg-[#161B27]' : 'bg-white'}>
                  {y}년
                </option>
              ))}
            </select>
          </div>

          <div className={`flex items-center gap-1 rounded-lg p-1 border ${filterBg}`}>
            {categories.map(cat => (
              <button
                key={cat.key}
                onClick={() => setFilter(cat.key)}
                className={`px-3.5 py-2 rounded-md text-[11px] font-semibold cursor-pointer whitespace-nowrap transition-all ${
                  filter === cat.key ? activeTab : inactiveTab
                }`}
              >
                {cat.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {loading && (
        <div className={`flex items-center justify-center gap-2 py-14 text-sm ${isDark ? 'text-[#8B9BB4]' : 'text-gray-500'}`}>
          <i className="ri-loader-4-line animate-spin text-lg"></i>
          보고서 목록 불러오는 중...
        </div>
      )}

      {!loading && error && (
        <div className={`text-center py-14 ${isDark ? 'text-[#8B9BB4]' : 'text-gray-500'}`}>
          <span className="w-12 h-12 flex items-center justify-center mx-auto mb-3 text-red-400">
            <i className="ri-error-warning-line text-3xl"></i>
          </span>
          <p className="text-sm mb-3">보고서 목록을 불러오지 못했습니다: {error}</p>
          <button
            onClick={reload}
            className="px-4 py-2 rounded-lg text-xs font-semibold bg-teal-500 text-white hover:bg-teal-600 cursor-pointer transition-colors"
          >
            다시 시도
          </button>
        </div>
      )}

      {!loading && !error && (
        <>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {displayedReports.map(report => (
              <ReportCard key={report.id} report={report} isDark={isDark} />
            ))}
          </div>

          {displayedReports.length === 0 && (
            <div className={`text-center py-14 ${isDark ? 'text-[#5A6A80]' : 'text-gray-400'}`}>
              <span className="w-12 h-12 flex items-center justify-center mx-auto mb-3">
                <i className="ri-file-search-line text-3xl"></i>
              </span>
              {searchQuery.trim() ? (
                <>
                  <p className="text-sm mb-1">검색 결과가 없습니다</p>
                  <p className="text-xs">다른 약제명이나 성분명으로 검색해보세요</p>
                </>
              ) : (
                <p className="text-sm">
                  {activeYear}년 {filter !== 'all' ? categories.find(c => c.key === filter)?.label : ''} 보고서가 없습니다
                </p>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
