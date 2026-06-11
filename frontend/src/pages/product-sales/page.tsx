import { useState } from 'react';
import {
  LineChart, Line, AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts';
import { productSalesData, quarterlyData } from '@/mocks/dashboardData';

const PRODUCTS = [
  { key: 'Nexavir', color: '#0D9488' },
  { key: 'Cardiomax', color: '#7C3AED' },
  { key: 'Oncovance', color: '#D97706' },
  { key: 'Diabecare', color: '#EF4444' },
  { key: 'Immunex', color: '#3B82F6' },
];

const CustomTooltip = ({ active, payload, label, isDark }: any) => {
  if (active && payload && payload.length) {
    const bg = isDark ? 'bg-[#1E2530] border-[#2A3545]' : 'bg-white border-gray-200 shadow-lg';
    const txt = isDark ? 'text-white' : 'text-gray-900';
    const sub = isDark ? 'text-[#8B9BB4]' : 'text-gray-500';
    return (
      <div className={`${bg} rounded-xl p-3`}>
        <p className={`text-xs font-bold mb-2 ${txt}`}>{label}</p>
        {payload.map((entry: any) => (
          <div key={entry.dataKey} className="flex items-center gap-2 text-xs mb-1">
            <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: entry.color }}></span>
            <span className={sub}>{entry.name}:</span>
            <span className={`font-semibold ${txt}`}>{entry.value.toLocaleString()}백만원</span>
          </div>
        ))}
      </div>
    );
  }
  return null;
};

export default function ProductSalesPage() {
  const [isDark, setIsDark] = useState(false);
  const [chartType, setChartType] = useState<'line' | 'area' | 'bar'>('area');
  const [period, setPeriod] = useState<'monthly' | 'quarterly'>('monthly');
  const [activeProducts, setActiveProducts] = useState<string[]>(PRODUCTS.map(p => p.key));

  const toggleProduct = (key: string) => { setActiveProducts(prev => prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key]); };
  const data = period === 'monthly' ? productSalesData : quarterlyData;
  const xKey = period === 'monthly' ? 'month' : 'quarter';

  const totalSales = productSalesData.reduce((sum, row) => sum + PRODUCTS.reduce((s, p) => s + ((row as any)[p.key] || 0), 0), 0);
  const topProduct = PRODUCTS.reduce((top, p) => { const total = productSalesData.reduce((s, row) => s + ((row as any)[p.key] || 0), 0); return total > top.total ? { key: p.key, total } : top; }, { key: '', total: 0 });
  const latestGrowth = (() => {
    const last = productSalesData[productSalesData.length - 1];
    const prev = productSalesData[productSalesData.length - 2];
    const lastTotal = PRODUCTS.reduce((s, p) => s + ((last as any)[p.key] || 0), 0);
    const prevTotal = PRODUCTS.reduce((s, p) => s + ((prev as any)[p.key] || 0), 0);
    return (((lastTotal - prevTotal) / prevTotal) * 100).toFixed(1);
  })();

  const pageBg = isDark ? 'bg-[#0D1117]' : 'bg-gray-50';
  const headerBorder = isDark ? 'border-[#1E2530]' : 'border-gray-200';
  const textMain = isDark ? 'text-white' : 'text-gray-900';
  const textSub = isDark ? 'text-[#8B9BB4]' : 'text-gray-500';
  const accentColor = isDark ? 'text-[#00E5CC]' : 'text-teal-600';
  const kpiBg = isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-white border-gray-200';
  const chartBg = isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-white border-gray-200';
  const tableBg = isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-white border-gray-200';
  const tableHeaderBg = isDark ? 'bg-[#1E2530]' : 'bg-gray-100';
  const tableBorder = isDark ? 'border-[#1E2530]' : 'border-gray-200';
  const tableStripe = isDark ? 'bg-[#1A2035]/20' : 'bg-gray-50/50';
  const tableHover = isDark ? 'hover:bg-[#00E5CC]/5' : 'hover:bg-teal-50/50';
  const tabBg = isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-gray-100 border-gray-200';
  const tabActive = isDark ? 'bg-[#00E5CC] text-[#0A0E1A]' : 'bg-teal-600 text-white';
  const tabInactive = isDark ? 'text-[#8B9BB4] hover:text-white' : 'text-gray-500 hover:text-gray-900';
  const iconToggle = isDark ? 'text-[#8B9BB4] hover:text-white' : 'text-gray-500 hover:text-gray-900';
  const iconActive = isDark ? 'bg-[#00E5CC] text-[#0A0E1A]' : 'bg-teal-600 text-white';
  const gridStroke = isDark ? '#1E2530' : '#E5E7EB';
  const tickFill = isDark ? '#8B9BB4' : '#6B7280';

  return (
    <div className={`min-h-screen ${pageBg} ${isDark ? 'text-white' : 'text-gray-900'}`}>
      <div className={`px-8 pt-8 pb-6 border-b ${headerBorder}`}>
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className={`w-5 h-5 flex items-center justify-center ${accentColor}`}><i className="ri-line-chart-line"></i></span>
              <h1 className={`text-2xl font-bold ${textMain}`}>Product Sales</h1>
            </div>
            <p className={`${textSub} text-sm`}>제품별 매출 추이 및 성과 분석</p>
          </div>
          <div className="flex items-center gap-3">
            <div className={`flex items-center gap-1 rounded-lg p-1 ${tabBg}`}>
              {[{ key: 'monthly', label: '월별' }, { key: 'quarterly', label: '분기별' }].map(tab => (
                <button key={tab.key} onClick={() => setPeriod(tab.key as any)}
                  className={`px-3 py-1.5 rounded-md text-xs font-medium cursor-pointer whitespace-nowrap transition-all ${period === tab.key ? tabActive : tabInactive}`}>{tab.label}</button>
              ))}
            </div>
            <div className={`flex items-center gap-1 rounded-lg p-1 ${tabBg}`}>
              {[{ key: 'area', icon: 'ri-landscape-line' }, { key: 'line', icon: 'ri-line-chart-line' }, { key: 'bar', icon: 'ri-bar-chart-2-line' }].map(tab => (
                <button key={tab.key} onClick={() => setChartType(tab.key as any)}
                  className={`w-8 h-8 flex items-center justify-center rounded-md cursor-pointer transition-all ${chartType === tab.key ? iconActive : iconToggle}`}>
                  <i className={`${tab.icon} text-sm`}></i>
                </button>
              ))}
            </div>
            <button onClick={() => setIsDark(!isDark)}
              className={`w-9 h-9 flex items-center justify-center rounded-lg cursor-pointer transition-all ${isDark ? 'bg-[#1E2530] text-amber-400 hover:bg-[#2A3545]' : 'bg-gray-100 text-gray-500 hover:bg-gray-200'}`}
              title={isDark ? '라이트 모드' : '다크 모드'}>
              <i className={isDark ? 'ri-sun-line text-lg' : 'ri-moon-line text-lg'}></i>
            </button>
          </div>
        </div>
      </div>

      <div className="px-8 py-6 space-y-5">
        <div className="grid grid-cols-4 gap-4">
          {[
            { label: '2025년 누적 매출', value: `₩${(totalSales / 1000).toFixed(1)}B`, sub: '백만원 기준', accent: true },
            { label: '최고 매출 제품', value: topProduct.key, sub: `₩${(topProduct.total / 1000).toFixed(1)}B`, accent: false, color: '#7C3AED' },
            { label: '전월 대비 성장률', value: `+${latestGrowth}%`, sub: '12월 기준', accent: false, color: '#10B981' },
            { label: '분석 제품 수', value: `${PRODUCTS.length}개`, sub: '활성 제품 기준', accent: false, color: '#D97706' },
          ].map((item, i) => (
            <div key={i} className={`rounded-xl p-4 border ${kpiBg}`}>
              <p className={`text-xs mb-1 ${textSub}`}>{item.label}</p>
              <p className="text-2xl font-bold" style={{ color: item.accent ? undefined : item.color }}>{item.value}</p>
              <p className={`text-xs mt-1 ${textSub}`}>{item.sub}</p>
            </div>
          ))}
        </div>

        <div className="flex items-center gap-3 flex-wrap">
          <span className={`text-xs ${textSub}`}>제품 필터:</span>
          {PRODUCTS.map(p => (
            <button key={p.key} onClick={() => toggleProduct(p.key)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium cursor-pointer whitespace-nowrap transition-all border ${
                activeProducts.includes(p.key) ? 'border-transparent text-white' : isDark ? 'border-[#2A3545] text-[#8B9BB4] bg-transparent' : 'border-gray-300 text-gray-500 bg-transparent'
              }`}
              style={activeProducts.includes(p.key) ? { backgroundColor: p.color } : {}}>
              <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ backgroundColor: activeProducts.includes(p.key) ? '#fff' : p.color }}></span>{p.key}
            </button>
          ))}
        </div>

        <div className={`rounded-2xl p-6 border ${chartBg}`}>
          <div className="mb-4"><h3 className={`font-bold text-base ${textMain}`}>제품별 매출 추이</h3><p className={`text-xs mt-0.5 ${textSub}`}>단위: 백만원</p></div>
          <ResponsiveContainer width="100%" height={320}>
            {chartType === 'area' ? (
              <AreaChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
                <defs>{PRODUCTS.map(p => (<linearGradient key={p.key} id={`grad-${p.key}`} x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor={p.color} stopOpacity={0.3} /><stop offset="95%" stopColor={p.color} stopOpacity={0} /></linearGradient>))}</defs>
                <CartesianGrid strokeDasharray="3 3" stroke={gridStroke} />
                <XAxis dataKey={xKey} tick={{ fill: tickFill, fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: tickFill, fontSize: 11 }} axisLine={false} tickLine={false} />
                <Tooltip content={<CustomTooltip isDark={isDark} />} />
                {PRODUCTS.map(p => activeProducts.includes(p.key) && <Area key={p.key} type="monotone" dataKey={p.key} stroke={p.color} fill={`url(#grad-${p.key})`} strokeWidth={2} dot={false} />)}
              </AreaChart>
            ) : chartType === 'line' ? (
              <LineChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={gridStroke} />
                <XAxis dataKey={xKey} tick={{ fill: tickFill, fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: tickFill, fontSize: 11 }} axisLine={false} tickLine={false} />
                <Tooltip content={<CustomTooltip isDark={isDark} />} />
                {PRODUCTS.map(p => activeProducts.includes(p.key) && <Line key={p.key} type="monotone" dataKey={p.key} stroke={p.color} strokeWidth={2.5} dot={false} activeDot={{ r: 5, strokeWidth: 0 }} />)}
              </LineChart>
            ) : (
              <BarChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={gridStroke} />
                <XAxis dataKey={xKey} tick={{ fill: tickFill, fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: tickFill, fontSize: 11 }} axisLine={false} tickLine={false} />
                <Tooltip content={<CustomTooltip isDark={isDark} />} />
                {PRODUCTS.map(p => activeProducts.includes(p.key) && <Bar key={p.key} dataKey={p.key} fill={p.color} radius={[4, 4, 0, 0]} />)}
              </BarChart>
            )}
          </ResponsiveContainer>
        </div>

        <div className={`rounded-2xl border overflow-hidden ${tableBg}`}>
          <div className={`px-6 py-4 border-b ${tableBorder}`}><h3 className={`font-bold text-base ${textMain}`}>제품별 성과 요약</h3></div>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className={tableHeaderBg}>
                  <th className={`text-left text-xs font-semibold px-6 py-3 ${textSub}`}>제품명</th>
                  <th className={`text-right text-xs font-semibold px-4 py-3 ${textSub}`}>1월</th>
                  <th className={`text-right text-xs font-semibold px-4 py-3 ${textSub}`}>6월</th>
                  <th className={`text-right text-xs font-semibold px-4 py-3 ${textSub}`}>12월</th>
                  <th className={`text-right text-xs font-semibold px-4 py-3 ${textSub}`}>연간 합계</th>
                  <th className={`text-center text-xs font-semibold px-4 py-3 ${textSub}`}>성장률</th>
                  <th className={`text-left text-xs font-semibold px-4 py-3 ${textSub}`}>추이</th>
                </tr>
              </thead>
              <tbody>
                {PRODUCTS.map((p, idx) => {
                  const jan = (productSalesData[0] as any)[p.key];
                  const jun = (productSalesData[5] as any)[p.key];
                  const dec = (productSalesData[11] as any)[p.key];
                  const total = productSalesData.reduce((s, row) => s + ((row as any)[p.key] || 0), 0);
                  const growth = (((dec - jan) / jan) * 100).toFixed(1);
                  return (
                    <tr key={p.key} className={`border-t ${tableBorder} ${tableHover} transition-colors ${idx % 2 === 1 ? tableStripe : ''}`}>
                      <td className="px-6 py-3">
                        <div className="flex items-center gap-2"><span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: p.color }}></span><span className={`text-sm font-semibold ${textMain}`}>{p.key}</span></div>
                      </td>
                      <td className={`px-4 py-3 text-sm text-right ${textSub}`}>{jan.toLocaleString()}</td>
                      <td className={`px-4 py-3 text-sm text-right ${textSub}`}>{jun.toLocaleString()}</td>
                      <td className={`px-4 py-3 text-sm font-semibold text-right ${textMain}`}>{dec.toLocaleString()}</td>
                      <td className={`px-4 py-3 text-sm font-bold text-right ${accentColor}`}>{total.toLocaleString()}</td>
                      <td className="px-4 py-3 text-center"><span className="text-emerald-500 text-xs font-semibold bg-emerald-50 px-2 py-1 rounded-full border border-emerald-200">+{growth}%</span></td>
                      <td className="px-4 py-3">
                        <div className="flex items-end gap-0.5 h-6">
                          {productSalesData.filter((_, i) => i % 2 === 0).map((row, i) => {
                            const val = (row as any)[p.key];
                            const maxVal = Math.max(...productSalesData.map(r => (r as any)[p.key]));
                            const height = Math.max(4, (val / maxVal) * 24);
                            return (<div key={i} className="w-1.5 rounded-sm flex-shrink-0" style={{ height, backgroundColor: p.color + '80' }}></div>);
                          })}
                        </div>
                      </td>
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