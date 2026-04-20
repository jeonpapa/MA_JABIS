import { useEffect, useMemo, useState } from 'react';
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Cell,
} from 'recharts';
import {
  fetchForeignDrugList,
  fetchForeignDrugDetail,
  searchForeignLive,
  deleteForeignDrug,
  type ForeignDrugListItem,
  type ForeignDrugDetail,
  type A8Pricing,
} from '@/api/foreign';

type FormFilter = 'all' | 'oral' | 'injection';

const FORM_FILTER_OPTIONS: { key: FormFilter; label: string; icon: string }[] = [
  { key: 'all', label: '전체', icon: 'ri-apps-2-line' },
  { key: 'oral', label: '경구제', icon: 'ri-capsule-line' },
  { key: 'injection', label: '주사제', icon: 'ri-syringe-line' },
];

const FORM_TYPE_LABEL: Record<string, string> = {
  oral: '경구제',
  injection: '주사제',
  unknown: '제형 미확인',
};
import { ApiError } from '@/api/client';

const A8_COUNTRIES = [
  { key: 'usa', label: '미국', flag: '🇺🇸', currency: 'USD' },
  { key: 'uk', label: '영국', flag: '🇬🇧', currency: 'GBP' },
  { key: 'germany', label: '독일', flag: '🇩🇪', currency: 'EUR' },
  { key: 'france', label: '프랑스', flag: '🇫🇷', currency: 'EUR' },
  { key: 'canada', label: '캐나다', flag: '🇨🇦', currency: 'CAD' },
  { key: 'japan', label: '일본', flag: '🇯🇵', currency: 'JPY' },
  { key: 'italy', label: '이탈리아', flag: '🇮🇹', currency: 'EUR' },
  { key: 'switzerland', label: '스위스', flag: '🇨🇭', currency: 'CHF' },
];

// HTA / Approval 국가는 라벨(한글 가나다순)으로 세로 정렬
const HTA_COUNTRIES = [
  { key: 'canada', label: '캐나다', flag: '🇨🇦', body: 'CADTH' },
  { key: 'scotland', label: '스코틀랜드', flag: '🏴󠁧󠁢󠁳󠁣󠁴󠁿', body: 'SMC' },
  { key: 'uk', label: '영국', flag: '🇬🇧', body: 'NICE' },
  { key: 'australia', label: '호주', flag: '🇦🇺', body: 'PBAC' },
].sort((a, b) => a.label.localeCompare(b.label, 'ko'));

const ALL_APPROVAL_COUNTRIES = [
  { key: 'germany', label: '독일', flag: '🇩🇪' },
  { key: 'usa', label: '미국', flag: '🇺🇸' },
  { key: 'uk', label: '영국', flag: '🇬🇧' },
  { key: 'italy', label: '이탈리아', flag: '🇮🇹' },
  { key: 'japan', label: '일본', flag: '🇯🇵' },
  { key: 'scotland', label: '스코틀랜드', flag: '🏴󠁧󠁢󠁳󠁣󠁴󠁿' },
  { key: 'switzerland', label: '스위스', flag: '🇨🇭' },
  { key: 'australia', label: '호주', flag: '🇦🇺' },
  { key: 'canada', label: '캐나다', flag: '🇨🇦' },
  { key: 'france', label: '프랑스', flag: '🇫🇷' },
].sort((a, b) => a.label.localeCompare(b.label, 'ko'));

const getCurrencySymbol = (currency: string) => {
  const map: Record<string, string> = { USD: '$', GBP: '£', JPY: '¥', CHF: 'Fr.', EUR: '€', CAD: 'CA$' };
  return map[currency] || currency;
};

const getStatusBadgeClass = (status: string) => {
  if (status === '권고') return 'bg-emerald-400/10 text-emerald-400 border border-emerald-400/20';
  if (status === '조건부 권고') return 'bg-amber-400/10 text-amber-400 border border-amber-400/20';
  if (status === '비권고') return 'bg-red-400/10 text-red-400 border border-red-400/20';
  if (status === '종료') return 'bg-[#4A5568]/20 text-[#8B9BB4] border border-[#4A5568]/30';
  return 'bg-[#4A5568]/20 text-[#8B9BB4]';
};

export default function InternationalPricingPage() {
  const [history, setHistory] = useState<ForeignDrugListItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState<string | null>(null);

  const [newSearchQuery, setNewSearchQuery] = useState('');
  const [searchDropdown, setSearchDropdown] = useState(false);
  const [liveSearching, setLiveSearching] = useState(false);

  const [selectedDrug, setSelectedDrug] = useState<ForeignDrugDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const [activeTab, setActiveTab] = useState<'pricing' | 'hta' | 'approval'>('pricing');
  const [expandedHta, setExpandedHta] = useState<string | null>(null);
  const [expandedApproval, setExpandedApproval] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ForeignDrugListItem | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [formFilter, setFormFilter] = useState<FormFilter>('all');

  const resolvePricing = (countryKey: string): A8Pricing | undefined => {
    if (!selectedDrug) return undefined;
    if (formFilter === 'all') return selectedDrug.a8Pricing[countryKey];
    const entries = selectedDrug.a8PricingByForm?.[countryKey] ?? [];
    return entries.find(e => e.formType === formFilter);
  };

  const loadHistory = async () => {
    setHistoryLoading(true);
    setHistoryError(null);
    try {
      const list = await fetchForeignDrugList();
      // 제품명(queryName) 기준 가나다/알파벳 정렬
      list.sort((a, b) => a.queryName.localeCompare(b.queryName, 'ko', { sensitivity: 'base' }));
      setHistory(list);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : '이력 조회 실패';
      setHistoryError(msg);
    } finally {
      setHistoryLoading(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deleteForeignDrug(deleteTarget.queryName);
      if (selectedDrug?.id === deleteTarget.id) {
        setSelectedDrug(null);
      }
      setDeleteTarget(null);
      await loadHistory();
    } catch (err) {
      alert(err instanceof ApiError ? err.message : '삭제 실패');
    } finally {
      setDeleting(false);
    }
  };

  const handleDownloadReport = () => {
    if (!selectedDrug) return;
    // 선택 약제의 A8 가격 / HTA / 허가 상세를 단일 xlsx 로 — 서버 exporter 가 아직 없어
    // 임시로 브라우저에서 CSV 를 조합해 다운로드 (3개 시트 대신 단일 CSV).
    const rows: string[][] = [];
    const filterLabel = FORM_FILTER_OPTIONS.find(o => o.key === formFilter)?.label ?? '전체';
    rows.push(['구분', '국가', '항목', '값']);
    rows.push(['필터', '제형', filterLabel, '']);
    for (const c of A8_COUNTRIES) {
      const p = resolvePricing(c.key);
      if (!p) { rows.push(['A8 가격', c.label, '가격', '정보 없음']); continue; }
      rows.push(['A8 가격', c.label, `가격(${p.currency})`, p.price.toLocaleString()]);
      rows.push(['A8 가격', c.label, '제형', FORM_TYPE_LABEL[p.formType] ?? p.formType]);
      if (p.krwConverted != null) rows.push(['A8 가격', c.label, 'KRW 환산', p.krwConverted.toLocaleString()]);
      rows.push(['A8 가격', c.label, '급여', p.reimbursed ? 'O' : 'X']);
      if (p.sourceLabel) rows.push(['A8 가격', c.label, '출처', p.sourceLabel]);
    }
    for (const c of HTA_COUNTRIES) {
      const h = selectedDrug.htaStatus[c.key];
      if (!h) { rows.push(['HTA', c.label, c.body, '평가 없음']); continue; }
      rows.push(['HTA', c.label, `${c.body} 권고`, h.recommendation]);
      if (h.date) rows.push(['HTA', c.label, '평가일', h.date]);
      if (h.note) rows.push(['HTA', c.label, '메모', h.note]);
    }
    for (const c of ALL_APPROVAL_COUNTRIES) {
      const a = selectedDrug.approvalStatus[c.key];
      if (!a) { rows.push(['허가', c.label, '상태', '데이터 없음']); continue; }
      rows.push(['허가', c.label, '상태', a.approved ? '허가' : '미허가']);
      if (a.indication) rows.push(['허가', c.label, '적응증 요약', a.indication]);
    }
    const csv = '\uFEFF' + rows.map(r => r.map(v => `"${(v || '').replace(/"/g, '""')}"`).join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const filterSuffix = formFilter === 'all' ? '' : `_${FORM_FILTER_OPTIONS.find(o => o.key === formFilter)?.label}`;
    a.download = `${selectedDrug.productName}${filterSuffix}_A8_HTA_허가_리포트.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  useEffect(() => { void loadHistory(); }, []);

  // KRW 조정가 국가간 비교 (HIRA 방식) — 필터 반영
  const adjustedChartData = useMemo(() => {
    if (!selectedDrug) return [];
    const rows = A8_COUNTRIES
      .map(c => {
        const p = resolvePricing(c.key);
        if (!p || p.adjustedPriceKrw == null) return null;
        return { key: c.key, label: `${c.flag} ${c.label}`, krw: Math.round(p.adjustedPriceKrw) };
      })
      .filter((r): r is { key: string; label: string; krw: number } => r !== null);
    rows.sort((a, b) => b.krw - a.krw);
    return rows;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedDrug, formFilter]);

  const searchResults = newSearchQuery.trim().length > 0
    ? history.filter(d =>
        d.queryName.toLowerCase().includes(newSearchQuery.toLowerCase()),
      )
    : [];

  const handleSelect = async (queryName: string) => {
    setNewSearchQuery('');
    setSearchDropdown(false);
    setActiveTab('pricing');
    setExpandedHta(null);
    setExpandedApproval(null);
    setDetailLoading(true);
    setDetailError(null);
    setSelectedDrug(null);
    try {
      const detail = await fetchForeignDrugDetail(queryName);
      setSelectedDrug(detail);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : '상세 조회 실패';
      setDetailError(msg);
    } finally {
      setDetailLoading(false);
    }
  };

  const handleNewSearch = async () => {
    const q = newSearchQuery.trim();
    if (!q) return;
    if (searchResults.length > 0) {
      await handleSelect(searchResults[0].queryName);
      return;
    }
    // 이력에 없는 경우 → 실시간 검색 트리거 후 이력 리로드
    setLiveSearching(true);
    setDetailError(null);
    try {
      await searchForeignLive(q);
      await loadHistory();
      await handleSelect(q);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : '실시간 검색 실패';
      setDetailError(msg);
    } finally {
      setLiveSearching(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#0D1117] text-white">
      {/* Header */}
      <div className="px-8 pt-8 pb-6 border-b border-[#1E2530]">
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="w-5 h-5 flex items-center justify-center"><i className="ri-global-line text-[#00E5CC]"></i></span>
              <h1 className="text-2xl font-bold text-white">해외약가</h1>
            </div>
            <p className="text-[#8B9BB4] text-sm">A8 국가 급여 약가 및 HTA · 허가 현황</p>
          </div>
          <button
            onClick={handleDownloadReport}
            disabled={!selectedDrug}
            title={selectedDrug ? 'A8 급여약가 · HTA · 허가 현황을 단일 CSV 로 내려받기' : '약제 선택 시 활성화'}
            className="flex items-center gap-2 bg-[#00E5CC] text-[#0A0E1A] text-sm font-semibold px-4 py-2 rounded-lg cursor-pointer whitespace-nowrap hover:bg-[#00C9B1] transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <span className="w-4 h-4 flex items-center justify-center"><i className="ri-download-2-line text-sm"></i></span>
            리포트 다운로드
          </button>
        </div>
      </div>

      <div className="px-8 py-6 space-y-5">

        {/* ── 기존 검색 이력 카드 ── */}
        <div>
          <div className="flex items-center gap-2 mb-3">
            <span className="w-4 h-4 flex items-center justify-center text-[#8B9BB4]"><i className="ri-history-line text-sm"></i></span>
            <p className="text-[#8B9BB4] text-xs font-semibold uppercase tracking-wider">기존 검색 이력</p>
            <span className="bg-[#1E2530] text-[#8B9BB4] text-xs px-2 py-0.5 rounded-full">{history.length}건</span>
          </div>
          {historyLoading && (
            <p className="text-[#4A5568] text-sm">이력 로드 중...</p>
          )}
          {historyError && (
            <p className="text-red-400 text-sm">⚠ {historyError}</p>
          )}
          {!historyLoading && !historyError && history.length === 0 && (
            <p className="text-[#4A5568] text-sm">검색 이력 없음 — 아래에서 성분명을 검색하세요</p>
          )}
          {!historyLoading && history.length > 0 && (
            <div className="grid grid-cols-4 gap-3">
              {history.map(drug => (
                <div
                  key={drug.id}
                  className={`relative group text-left p-4 rounded-2xl border transition-all ${
                    selectedDrug?.id === drug.id
                      ? 'bg-[#00E5CC]/8 border-[#00E5CC]/40'
                      : 'bg-[#161B27] border-[#1E2530] hover:border-[#2A3545]'
                  }`}
                >
                  <button
                    onClick={() => void handleSelect(drug.queryName)}
                    className="absolute inset-0 w-full h-full cursor-pointer rounded-2xl"
                    aria-label={`${drug.queryName} 상세 조회`}
                  ></button>
                  <div className="relative flex items-start justify-between mb-3 pointer-events-none">
                    <div className="flex-1 min-w-0">
                      <p className={`text-sm font-bold truncate ${selectedDrug?.id === drug.id ? 'text-[#00E5CC]' : 'text-white group-hover:text-[#00E5CC]'} transition-colors`}>
                        {drug.queryName}
                      </p>
                      <p className="text-[#8B9BB4] text-xs mt-0.5 truncate">{drug.countryCount}개 국가</p>
                    </div>
                    {selectedDrug?.id === drug.id && (
                      <span className="w-5 h-5 flex items-center justify-center text-[#00E5CC] flex-shrink-0 ml-2">
                        <i className="ri-checkbox-circle-fill text-sm"></i>
                      </span>
                    )}
                  </div>
                  <div className="relative flex items-center justify-between pointer-events-none">
                    <div className="flex items-center gap-1.5">
                      <span className="w-3.5 h-3.5 flex items-center justify-center text-[#4A5568]"><i className="ri-calendar-line text-xs"></i></span>
                      <span className="text-[#4A5568] text-xs">{drug.lastSearchedAt}</span>
                    </div>
                    <button
                      onClick={(e) => { e.stopPropagation(); setDeleteTarget(drug); }}
                      title="검색 이력 삭제"
                      className="pointer-events-auto w-6 h-6 flex items-center justify-center rounded-md text-[#4A5568] hover:text-red-400 hover:bg-red-400/10 opacity-0 group-hover:opacity-100 transition-all cursor-pointer"
                    >
                      <i className="ri-delete-bin-line text-sm"></i>
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Delete Confirmation Modal */}
        {deleteTarget && (
          <div className="fixed inset-0 z-50 flex items-center justify-center">
            <div className="absolute inset-0 bg-black/60" onClick={() => !deleting && setDeleteTarget(null)}></div>
            <div className="relative bg-[#161B27] border border-red-400/40 rounded-2xl w-full max-w-md mx-4 p-6 shadow-2xl">
              <div className="flex items-center gap-3 mb-4">
                <div className="w-10 h-10 rounded-full bg-red-400/10 flex items-center justify-center flex-shrink-0">
                  <i className="ri-alert-line text-red-400 text-lg"></i>
                </div>
                <div>
                  <h3 className="text-white font-bold text-base">검색 이력 삭제</h3>
                  <p className="text-[#8B9BB4] text-xs mt-0.5">{deleteTarget.queryName}</p>
                </div>
              </div>
              <div className="bg-red-400/5 border border-red-400/20 rounded-xl p-4 mb-5">
                <p className="text-red-400 text-xs font-semibold mb-1">⚠ 복구 어려움 안내</p>
                <p className="text-[#8B9BB4] text-xs leading-relaxed">
                  이 약제의 A8 국가 급여약가, HTA 평가, 허가 캐시가 모두 삭제됩니다.
                  다시 사용하려면 신규 검색으로 스크레이핑을 재실행해야 하며, 소스 사이트 변경으로 인해 동일한 결과를 보장하지 않습니다.
                </p>
              </div>
              <div className="flex items-center gap-2 justify-end">
                <button
                  onClick={() => setDeleteTarget(null)}
                  disabled={deleting}
                  className="text-[#8B9BB4] hover:text-white text-sm px-4 py-2 rounded-lg cursor-pointer transition-colors disabled:opacity-40"
                >
                  취소
                </button>
                <button
                  onClick={() => void handleDelete()}
                  disabled={deleting}
                  className="flex items-center gap-2 bg-red-500 hover:bg-red-600 text-white text-sm font-semibold px-4 py-2 rounded-lg cursor-pointer whitespace-nowrap transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  {deleting ? (
                    <>
                      <i className="ri-loader-4-line animate-spin text-sm"></i>
                      삭제 중...
                    </>
                  ) : (
                    <>
                      <i className="ri-delete-bin-line text-sm"></i>
                      삭제
                    </>
                  )}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* ── 신규 검색 ── */}
        <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5">
          <div className="flex items-center gap-2 mb-3">
            <span className="w-4 h-4 flex items-center justify-center text-[#00E5CC]"><i className="ri-search-2-line text-sm"></i></span>
            <p className="text-white text-sm font-semibold">신규 검색</p>
            <span className="text-[#4A5568] text-xs">이력에 있으면 즉시 조회, 없으면 스크레이핑 실행</span>
          </div>
          <div className="flex gap-3 relative">
            <div className="flex-1 relative">
              <div className="flex items-center gap-3 bg-[#0D1117] border border-[#1E2530] rounded-xl px-4 py-3 focus-within:border-[#00E5CC]/50 transition-colors">
                <span className="w-4 h-4 flex items-center justify-center text-[#8B9BB4] flex-shrink-0">
                  <i className="ri-search-line text-sm"></i>
                </span>
                <input
                  type="text"
                  placeholder="영문 성분명 또는 제품명 입력 (예: Pembrolizumab, Keytruda)"
                  value={newSearchQuery}
                  onChange={e => { setNewSearchQuery(e.target.value); setSearchDropdown(true); }}
                  onFocus={() => newSearchQuery && setSearchDropdown(true)}
                  onKeyDown={e => e.key === 'Enter' && void handleNewSearch()}
                  className="flex-1 bg-transparent text-white text-sm placeholder-[#4A5568] focus:outline-none"
                />
                {newSearchQuery && (
                  <button
                    onClick={() => { setNewSearchQuery(''); setSearchDropdown(false); }}
                    className="w-4 h-4 flex items-center justify-center text-[#4A5568] hover:text-white cursor-pointer transition-colors"
                  >
                    <i className="ri-close-line text-sm"></i>
                  </button>
                )}
              </div>

              {/* Dropdown */}
              {searchDropdown && searchResults.length > 0 && (
                <div className="absolute top-full left-0 right-0 mt-1 bg-[#161B27] border border-[#1E2530] rounded-xl overflow-hidden z-50">
                  {searchResults.map(d => (
                    <button
                      key={d.id}
                      onClick={() => void handleSelect(d.queryName)}
                      className="w-full flex items-center gap-3 px-4 py-3 hover:bg-[#00E5CC]/8 transition-colors cursor-pointer text-left border-b border-[#1E2530] last:border-0"
                    >
                      <span className="w-7 h-7 rounded-lg bg-[#00E5CC]/10 flex items-center justify-center flex-shrink-0">
                        <i className="ri-capsule-line text-[#00E5CC] text-xs"></i>
                      </span>
                      <div className="flex-1 min-w-0">
                        <p className="text-white text-sm font-semibold">{d.queryName}</p>
                        <p className="text-[#8B9BB4] text-xs">{d.countryCount}개 국가 · {d.lastSearchedAt}</p>
                      </div>
                      <span className="text-[#4A5568] text-xs whitespace-nowrap">검색 이력 있음</span>
                    </button>
                  ))}
                </div>
              )}
              {searchDropdown && newSearchQuery && searchResults.length === 0 && !liveSearching && (
                <div className="absolute top-full left-0 right-0 mt-1 bg-[#161B27] border border-[#1E2530] rounded-xl px-4 py-3 z-50">
                  <p className="text-[#4A5568] text-sm">이력 없음 — 검색 버튼 클릭 시 실시간 스크레이핑</p>
                </div>
              )}
            </div>
            <button
              onClick={() => void handleNewSearch()}
              disabled={liveSearching || !newSearchQuery.trim()}
              className="flex items-center gap-2 bg-[#00E5CC] text-[#0A0E1A] text-sm font-bold px-5 py-3 rounded-xl cursor-pointer whitespace-nowrap hover:bg-[#00C9B1] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {liveSearching ? (
                <>
                  <span className="w-4 h-4 flex items-center justify-center"><i className="ri-loader-4-line animate-spin text-sm"></i></span>
                  검색 중...
                </>
              ) : (
                <>
                  <span className="w-4 h-4 flex items-center justify-center"><i className="ri-search-line text-sm"></i></span>
                  검색
                </>
              )}
            </button>
          </div>
        </div>

        {/* ── 상세 패널 ── */}
        {detailLoading && (
          <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] py-16 text-center">
            <span className="w-12 h-12 flex items-center justify-center mx-auto mb-3 text-[#00E5CC]">
              <i className="ri-loader-4-line text-3xl animate-spin"></i>
            </span>
            <p className="text-[#8B9BB4] text-sm">상세 정보 조회 중...</p>
          </div>
        )}
        {detailError && !detailLoading && (
          <div className="bg-red-900/20 border border-red-400/30 rounded-2xl py-6 px-5">
            <p className="text-red-400 text-sm">⚠ {detailError}</p>
          </div>
        )}

        {selectedDrug && !detailLoading ? (
          <div className="space-y-4">
            {/* Product Header + Tabs */}
            <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] px-6 py-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-4">
                  <div className="w-10 h-10 rounded-xl bg-[#00E5CC]/10 flex items-center justify-center flex-shrink-0">
                    <i className="ri-capsule-line text-[#00E5CC] text-lg"></i>
                  </div>
                  <div>
                    <h2 className="text-white text-lg font-bold">{selectedDrug.productName}</h2>
                    {selectedDrug.ingredient
                      && selectedDrug.ingredient.toLowerCase() !== selectedDrug.productName.toLowerCase() && (
                        <p className="text-[#8B9BB4] text-sm">{selectedDrug.ingredient}</p>
                      )}
                  </div>
                  <div className="flex items-center gap-3 ml-4 pl-4 border-l border-[#1E2530]">
                    <div className="flex items-center gap-1.5">
                      <span className="w-3.5 h-3.5 flex items-center justify-center text-[#4A5568]"><i className="ri-calendar-line text-xs"></i></span>
                      <span className="text-[#4A5568] text-xs">{selectedDrug.searchedAt || '-'}</span>
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-1 bg-[#0D1117] border border-[#1E2530] rounded-xl p-1">
                  {[
                    { key: 'pricing', label: 'A8 급여약가', icon: 'ri-money-dollar-circle-line' },
                    { key: 'hta', label: 'HTA 현황', icon: 'ri-shield-check-line' },
                    { key: 'approval', label: '허가 현황', icon: 'ri-file-check-line' },
                  ].map(tab => (
                    <button
                      key={tab.key}
                      onClick={() => setActiveTab(tab.key as 'pricing' | 'hta' | 'approval')}
                      className={`flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium cursor-pointer transition-all whitespace-nowrap ${
                        activeTab === tab.key ? 'bg-[#00E5CC] text-[#0A0E1A]' : 'text-[#8B9BB4] hover:text-white'
                      }`}
                    >
                      <span className="w-4 h-4 flex items-center justify-center"><i className={`${tab.icon} text-xs`}></i></span>
                      {tab.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {/* ── A8 Pricing Tab ── */}
            {activeTab === 'pricing' && (
              <div className="space-y-4">
              <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] overflow-hidden">
                <div className="px-5 py-4 border-b border-[#1E2530] flex items-center justify-between gap-3">
                  <div>
                    <h3 className="text-white font-bold text-sm">A8 국가 급여 약가</h3>
                    <p className="text-[#4A5568] text-xs mt-0.5">미국 · 영국 · 독일 · 프랑스 · 캐나다 · 일본 · 이탈리아 · 스위스</p>
                  </div>
                  <div className="flex items-center gap-1 bg-[#0D1117] border border-[#1E2530] rounded-xl p-1 flex-shrink-0">
                    {FORM_FILTER_OPTIONS.map(opt => (
                      <button
                        key={opt.key}
                        onClick={() => setFormFilter(opt.key)}
                        className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium cursor-pointer transition-all whitespace-nowrap ${
                          formFilter === opt.key ? 'bg-[#00E5CC] text-[#0A0E1A]' : 'text-[#8B9BB4] hover:text-white'
                        }`}
                      >
                        <span className="w-3.5 h-3.5 flex items-center justify-center"><i className={`${opt.icon} text-xs`}></i></span>
                        {opt.label}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="grid grid-cols-4 gap-0">
                  {A8_COUNTRIES.map((country, idx) => {
                    const pricing = resolvePricing(country.key);
                    return (
                      <div
                        key={country.key}
                        className={`p-5 ${idx % 4 !== 3 ? 'border-r border-[#1E2530]' : ''} ${idx >= 4 ? 'border-t border-[#1E2530]' : ''}`}
                      >
                        <div className="flex items-center gap-2 mb-3">
                          <span className="text-xl">{country.flag}</span>
                          <div className="flex-1 min-w-0">
                            <p className="text-white text-xs font-semibold">{country.label}</p>
                            <p className="text-[#4A5568] text-xs">{country.currency}</p>
                          </div>
                          {pricing?.reimbursed && (
                            <span className="text-xs px-1.5 py-0.5 rounded-full bg-emerald-400/10 text-emerald-400 font-semibold whitespace-nowrap">급여</span>
                          )}
                        </div>
                        {pricing ? (
                          <>
                            <div className="flex items-center gap-1.5 mb-0.5">
                              <p className="text-white text-sm font-semibold">
                                {getCurrencySymbol(country.currency)}{pricing.price.toLocaleString()}
                              </p>
                              <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${
                                pricing.formType === 'oral'
                                  ? 'bg-[#7C3AED]/10 text-[#B794F6] border border-[#7C3AED]/30'
                                  : pricing.formType === 'injection'
                                    ? 'bg-[#00E5CC]/10 text-[#00E5CC] border border-[#00E5CC]/30'
                                    : 'bg-[#4A5568]/20 text-[#8B9BB4] border border-[#4A5568]/30'
                              }`}>
                                {FORM_TYPE_LABEL[pricing.formType] ?? pricing.formType}
                              </span>
                            </div>
                            {pricing.krwConverted != null && (
                              <p className="text-[#4A5568] text-xs mb-2">환산 ₩{pricing.krwConverted.toLocaleString()}</p>
                            )}
                            {pricing.adjustedPriceKrw != null ? (
                              <div className="pt-2 border-t border-[#1E2530]">
                                <p className="text-[#4A5568] text-[10px] uppercase tracking-wider mb-0.5">HIRA 조정가</p>
                                <p className="text-[#00E5CC] text-base font-bold">
                                  ₩{pricing.adjustedPriceKrw.toLocaleString()}
                                </p>
                              </div>
                            ) : (
                              <div className="pt-2 border-t border-[#1E2530]">
                                <p className="text-[#4A5568] text-[10px]">조정가 산출 불가</p>
                              </div>
                            )}
                            {pricing.sourceLabel && (
                              <p className="text-[#8B9BB4] text-xs mt-2 truncate" title={pricing.sourceLabel}>
                                출처: {pricing.sourceLabel}
                              </p>
                            )}
                          </>
                        ) : (
                          <p className="text-[#4A5568] text-sm">정보 없음</p>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* KRW 조정가 국가 비교 */}
              {adjustedChartData.length > 0 && (
                <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] overflow-hidden">
                  <div className="px-5 py-4 border-b border-[#1E2530]">
                    <h3 className="text-white font-bold text-sm">KRW 조정가 국가 비교 (HIRA 방식)</h3>
                    <p className="text-[#4A5568] text-xs mt-0.5">공장도비율 · VAT · 유통마진 반영 · 높은 순 정렬 · 최대/최소 강조</p>
                  </div>
                  <div className="p-5">
                    <ResponsiveContainer width="100%" height={Math.max(200, adjustedChartData.length * 44)}>
                      <BarChart
                        data={adjustedChartData}
                        layout="vertical"
                        margin={{ top: 5, right: 40, left: 20, bottom: 5 }}
                      >
                        <CartesianGrid strokeDasharray="3 3" stroke="#1E2530" horizontal={false} />
                        <XAxis
                          type="number"
                          tick={{ fill: '#8B9BB4', fontSize: 11 }}
                          axisLine={false}
                          tickLine={false}
                          tickFormatter={v => `₩${(v as number).toLocaleString()}`}
                        />
                        <YAxis
                          type="category"
                          dataKey="label"
                          tick={{ fill: '#E5E7EB', fontSize: 12 }}
                          axisLine={false}
                          tickLine={false}
                          width={110}
                        />
                        <Tooltip
                          contentStyle={{ backgroundColor: '#0D1117', border: '1px solid #2A3545', borderRadius: '0.5rem', fontSize: 12 }}
                          cursor={{ fill: 'rgba(0, 229, 204, 0.05)' }}
                          formatter={(v: number) => [`₩${v.toLocaleString()}`, 'KRW 조정가']}
                        />
                        <Bar dataKey="krw" radius={[0, 6, 6, 0]} label={{ position: 'right', fill: '#E5E7EB', fontSize: 11, formatter: (v: number) => `₩${v.toLocaleString()}` }}>
                          {adjustedChartData.map((row, i) => {
                            const max = adjustedChartData[0].krw;
                            const min = adjustedChartData[adjustedChartData.length - 1].krw;
                            const color = row.krw === max ? '#00E5CC' : row.krw === min ? '#F59E0B' : '#4A5568';
                            return <Cell key={`cell-${i}`} fill={color} />;
                          })}
                        </Bar>
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                  <div className="px-5 pb-5 text-[11px] leading-relaxed text-[#8B9BB4] space-y-1 border-t border-[#1E2530] pt-3">
                    <div><span className="text-white font-semibold">산출 공식:</span> 현지약가 × 공장도비율 = 공장도가 → × 환율 = 공장도가(KRW) → × (1+VAT) → × (1+유통마진) = <span className="text-[#00E5CC] font-semibold">조정가</span></div>
                    <div><span className="text-white font-semibold">공장도비율:</span> US 0.74 · UK 0.73 · JP 0.79 · FR 0.77 · IT (AIFA Ex-factory 직접) · CH 0.73 · DE 특수공식</div>
                    <div><span className="text-white font-semibold">환율:</span> KEB하나은행 매매기준율 36개월 평균 · <span className="text-white font-semibold">유통거래폭:</span> 0% (A8 규정상 미적용)</div>
                  </div>
                </div>
              )}
              </div>
            )}

            {/* ── HTA Tab ── */}
            {activeTab === 'hta' && (
              <div className="space-y-3">
                <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] px-5 py-3">
                  <h3 className="text-white font-bold text-sm">HTA 중심 국가 평가 현황</h3>
                  <p className="text-[#4A5568] text-xs mt-0.5">영국(NICE) · 캐나다(CADTH) · 호주(PBAC) · 스코틀랜드(SMC)</p>
                </div>
                {HTA_COUNTRIES.map(country => {
                  const hta = selectedDrug.htaStatus[country.key];
                  const isExpanded = expandedHta === country.key;
                  return (
                    <div key={country.key} className="bg-[#161B27] rounded-2xl border border-[#1E2530] overflow-hidden">
                      <button
                        onClick={() => hta && setExpandedHta(isExpanded ? null : country.key)}
                        className={`w-full flex items-center gap-4 px-5 py-4 transition-colors text-left ${hta ? 'hover:bg-[#1E2530]/50 cursor-pointer' : 'cursor-default'}`}
                      >
                        <span className="text-2xl flex-shrink-0">{country.flag}</span>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 mb-1">
                            <p className="text-white text-sm font-bold">{country.label}</p>
                            <span className="text-[#4A5568] text-xs">({country.body})</span>
                            {hta && (
                              <span className={`text-xs px-2 py-0.5 rounded-full font-semibold ${getStatusBadgeClass(hta.status)}`}>
                                {hta.recommendation}
                              </span>
                            )}
                          </div>
                          {hta ? (
                            <div className="flex items-center gap-3">
                              <span className="text-[#8B9BB4] text-xs">평가일: {hta.date || '-'}</span>
                              <span className="text-[#4A5568] text-xs">|</span>
                              <span className="text-[#8B9BB4] text-xs truncate">{hta.note}</span>
                            </div>
                          ) : (
                            <p className="text-[#4A5568] text-xs">평가 정보 없음</p>
                          )}
                        </div>
                        {hta && (
                          <div className="flex items-center gap-2 flex-shrink-0">
                            <span className="text-[#00E5CC] text-xs font-medium whitespace-nowrap">
                              {isExpanded ? '접기' : '상세 문구 보기'}
                            </span>
                            <span className="w-5 h-5 flex items-center justify-center text-[#00E5CC]">
                              <i className={`text-sm transition-transform duration-200 ${isExpanded ? 'ri-arrow-up-s-line' : 'ri-arrow-down-s-line'}`}></i>
                            </span>
                          </div>
                        )}
                      </button>

                      {isExpanded && hta && (
                        <div className="px-5 pb-5 border-t border-[#1E2530]">
                          <div className="mt-4 bg-[#0D1117] rounded-xl p-4 border border-[#1E2530]">
                            <div className="flex items-center gap-2 mb-3">
                              <span className="w-4 h-4 flex items-center justify-center text-[#00E5CC]"><i className="ri-file-text-line text-xs"></i></span>
                              <p className="text-[#00E5CC] text-xs font-semibold uppercase tracking-wider">Official {country.body} Statement</p>
                            </div>
                            <p className="text-[#8B9BB4] text-xs leading-relaxed">{hta.fullText}</p>
                            {hta.detailUrl && (
                              <a
                                href={hta.detailUrl}
                                target="_blank"
                                rel="noreferrer"
                                className="inline-flex items-center gap-1.5 mt-3 text-[#00E5CC] text-xs font-semibold hover:underline"
                              >
                                <i className="ri-external-link-line"></i> 원문 바로가기
                              </a>
                            )}
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            {/* ── Approval Tab ── */}
            {activeTab === 'approval' && (
              <div className="space-y-3">
                <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] px-5 py-3">
                  <h3 className="text-white font-bold text-sm">제외국 허가 현황</h3>
                  <p className="text-[#4A5568] text-xs mt-0.5">FDA · EMA · PMDA · MHRA · TGA 매트릭스 기반 · 캐나다/스위스 데이터 소스 미구현</p>
                </div>
                {ALL_APPROVAL_COUNTRIES.map(country => {
                  const approval = selectedDrug.approvalStatus[country.key];
                  const isExpanded = expandedApproval === country.key;
                  const isApproved = approval?.approved;
                  const hasData = approval !== undefined;
                  return (
                    <div key={country.key} className="bg-[#161B27] rounded-2xl border border-[#1E2530] overflow-hidden">
                      <button
                        onClick={() => isApproved && setExpandedApproval(isExpanded ? null : country.key)}
                        className={`w-full flex items-center gap-4 px-5 py-4 transition-colors text-left ${isApproved ? 'hover:bg-[#1E2530]/50 cursor-pointer' : 'cursor-default'}`}
                      >
                        <span className="text-2xl flex-shrink-0">{country.flag}</span>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 mb-1">
                            <p className="text-white text-sm font-bold">{country.label}</p>
                            {!hasData ? (
                              <span className="text-xs px-2 py-0.5 rounded-full bg-[#4A5568]/20 text-[#8B9BB4] border border-[#4A5568]/30 font-semibold">데이터 없음</span>
                            ) : isApproved ? (
                              <span className="text-xs px-2 py-0.5 rounded-full bg-emerald-400/10 text-emerald-400 border border-emerald-400/20 font-semibold">허가</span>
                            ) : (
                              <span className="text-xs px-2 py-0.5 rounded-full bg-red-400/10 text-red-400 border border-red-400/20 font-semibold">미허가</span>
                            )}
                          </div>
                          {isApproved && approval ? (
                            <div className="flex items-center gap-3">
                              <span className="text-[#8B9BB4] text-xs truncate">{approval.indication}</span>
                            </div>
                          ) : (
                            <p className="text-[#4A5568] text-xs">{hasData ? '허가 정보 없음' : '데이터 소스 미구현'}</p>
                          )}
                        </div>
                        {isApproved && approval?.fullIndication && (
                          <div className="flex items-center gap-2 flex-shrink-0">
                            <span className="text-[#00E5CC] text-xs font-medium whitespace-nowrap">
                              {isExpanded ? '접기' : '상세 허가 문구'}
                            </span>
                            <span className="w-5 h-5 flex items-center justify-center text-[#00E5CC]">
                              <i className={`text-sm transition-transform duration-200 ${isExpanded ? 'ri-arrow-up-s-line' : 'ri-arrow-down-s-line'}`}></i>
                            </span>
                          </div>
                        )}
                      </button>

                      {isExpanded && approval?.fullIndication && (
                        <div className="px-5 pb-5 border-t border-[#1E2530]">
                          <div className="mt-4 bg-[#0D1117] rounded-xl p-4 border border-[#1E2530]">
                            <div className="flex items-center gap-2 mb-3">
                              <span className="w-4 h-4 flex items-center justify-center text-[#00E5CC]"><i className="ri-file-check-line text-xs"></i></span>
                              <p className="text-[#00E5CC] text-xs font-semibold uppercase tracking-wider">Approved Indications Summary</p>
                            </div>
                            <p className="text-[#8B9BB4] text-xs leading-relaxed">{approval.fullIndication}</p>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        ) : !detailLoading && !detailError && (
          <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] py-16 text-center">
            <span className="w-12 h-12 flex items-center justify-center mx-auto mb-3 text-[#4A5568]">
              <i className="ri-global-line text-4xl"></i>
            </span>
            <p className="text-[#8B9BB4] text-sm mb-1">검색 이력에서 제품을 선택하거나</p>
            <p className="text-[#4A5568] text-xs">신규 검색으로 성분명 또는 제품명을 입력하세요</p>
          </div>
        )}
      </div>
    </div>
  );
}
