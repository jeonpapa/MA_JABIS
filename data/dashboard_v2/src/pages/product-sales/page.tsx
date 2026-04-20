import { useState } from 'react';
import {
  LineChart, Line, AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts';
import { productSalesData, quarterlyData } from '@/mocks/dashboardData';

const PRODUCTS = [
  { key: 'Nexavir', color: '#00E5CC' },
  { key: 'Cardiomax', color: '#7C3AED' },
  { key: 'Oncovance', color: '#F59E0B' },
  { key: 'Diabecare', color: '#EF4444' },
  { key: 'Immunex', color: '#3B82F6' },
];

const CustomTooltip = ({ active, payload, label }: any) => {
  if (active && payload && payload.length) {
    return (
      <div className="bg-[#1E2530] border border-[#2A3545] rounded-xl p-3 shadow-xl">
        <p className="text-white text-xs font-bold mb-2">{label}</p>
        {payload.map((entry: any) => (
          <div key={entry.dataKey} className="flex items-center gap-2 text-xs mb-1">
            <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: entry.color }}></span>
            <span className="text-[#8B9BB4]">{entry.name}:</span>
            <span className="text-white font-semibold">{entry.value.toLocaleString()}백만원</span>
          </div>
        ))}
      </div>
    );
  }
  return null;
};

export default function ProductSalesPage() {
  const [chartType, setChartType] = useState<'line' | 'area' | 'bar'>('area');
  const [period, setPeriod] = useState<'monthly' | 'quarterly'>('monthly');
  const [activeProducts, setActiveProducts] = useState<string[]>(PRODUCTS.map(p => p.key));

  const toggleProduct = (key: string) => {
    setActiveProducts(prev =>
      prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key]
    );
  };

  const data = period === 'monthly' ? productSalesData : quarterlyData;
  const xKey = period === 'monthly' ? 'month' : 'quarter';

  const totalSales = productSalesData.reduce((sum, row) => {
    return sum + PRODUCTS.reduce((s, p) => s + ((row as any)[p.key] || 0), 0);
  }, 0);

  const topProduct = PRODUCTS.reduce((top, p) => {
    const total = productSalesData.reduce((s, row) => s + ((row as any)[p.key] || 0), 0);
    return total > top.total ? { key: p.key, total } : top;
  }, { key: '', total: 0 });

  const latestGrowth = (() => {
    const last = productSalesData[productSalesData.length - 1];
    const prev = productSalesData[productSalesData.length - 2];
    const lastTotal = PRODUCTS.reduce((s, p) => s + ((last as any)[p.key] || 0), 0);
    const prevTotal = PRODUCTS.reduce((s, p) => s + ((prev as any)[p.key] || 0), 0);
    return (((lastTotal - prevTotal) / prevTotal) * 100).toFixed(1);
  })();

  return (
    <div className="min-h-screen bg-[#0D1117] text-white">
      {/* Header */}
      <div className="px-8 pt-8 pb-6 border-b border-[#1E2530]">
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="w-5 h-5 flex items-center justify-center"><i className="ri-line-chart-line text-[#00E5CC]"></i></span>
              <h1 className="text-2xl font-bold text-white">Product Sales</h1>
            </div>
            <p className="text-[#8B9BB4] text-sm">제품별 매출 추이 및 성과 분석</p>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-1 bg-[#161B27] border border-[#1E2530] rounded-lg p-1">
              {[
                { key: 'monthly', label: '월별' },
                { key: 'quarterly', label: '분기별' },
              ].map(tab => (
                <button
                  key={tab.key}
                  onClick={() => setPeriod(tab.key as any)}
                  className={`px-3 py-1.5 rounded-md text-xs font-medium cursor-pointer whitespace-nowrap transition-all ${
                    period === tab.key ? 'bg-[#00E5CC] text-[#0A0E1A]' : 'text-[#8B9BB4] hover:text-white'
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-1 bg-[#161B27] border border-[#1E2530] rounded-lg p-1">
              {[
                { key: 'area', icon: 'ri-landscape-line' },
                { key: 'line', icon: 'ri-line-chart-line' },
                { key: 'bar', icon: 'ri-bar-chart-2-line' },
              ].map(tab => (
                <button
                  key={tab.key}
                  onClick={() => setChartType(tab.key as any)}
                  className={`w-8 h-8 flex items-center justify-center rounded-md cursor-pointer transition-all ${
                    chartType === tab.key ? 'bg-[#00E5CC] text-[#0A0E1A]' : 'text-[#8B9BB4] hover:text-white'
                  }`}
                >
                  <i className={`${tab.icon} text-sm`}></i>
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="px-8 py-6 space-y-5">
        {/* KPI Row */}
        <div className="grid grid-cols-4 gap-4">
          <div className="bg-[#161B27] rounded-xl p-4 border border-[#1E2530]">
            <p className="text-[#8B9BB4] text-xs mb-1">2025년 누적 매출</p>
            <p className="text-2xl font-bold text-[#00E5CC]">₩{(totalSales / 1000).toFixed(1)}B</p>
            <p className="text-[#8B9BB4] text-xs mt-1">백만원 기준</p>
          </div>
          <div className="bg-[#161B27] rounded-xl p-4 border border-[#1E2530]">
            <p className="text-[#8B9BB4] text-xs mb-1">최고 매출 제품</p>
            <p className="text-2xl font-bold text-[#7C3AED]">{topProduct.key}</p>
            <p className="text-[#8B9BB4] text-xs mt-1">₩{(topProduct.total / 1000).toFixed(1)}B</p>
          </div>
          <div className="bg-[#161B27] rounded-xl p-4 border border-[#1E2530]">
            <p className="text-[#8B9BB4] text-xs mb-1">전월 대비 성장률</p>
            <p className="text-2xl font-bold text-emerald-400">+{latestGrowth}%</p>
            <p className="text-[#8B9BB4] text-xs mt-1">12월 기준</p>
          </div>
          <div className="bg-[#161B27] rounded-xl p-4 border border-[#1E2530]">
            <p className="text-[#8B9BB4] text-xs mb-1">분석 제품 수</p>
            <p className="text-2xl font-bold text-[#F59E0B]">{PRODUCTS.length}<span className="text-[#8B9BB4] text-sm ml-1">개</span></p>
            <p className="text-[#8B9BB4] text-xs mt-1">활성 제품 기준</p>
          </div>
        </div>

        {/* Product Filter */}
        <div className="flex items-center gap-3 flex-wrap">
          <span className="text-[#8B9BB4] text-xs">제품 필터:</span>
          {PRODUCTS.map(p => (
            <button
              key={p.key}
              onClick={() => toggleProduct(p.key)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium cursor-pointer whitespace-nowrap transition-all border ${
                activeProducts.includes(p.key)
                  ? 'border-transparent text-[#0A0E1A]'
                  : 'border-[#2A3545] text-[#8B9BB4] bg-transparent'
              }`}
              style={activeProducts.includes(p.key) ? { backgroundColor: p.color } : {}}
            >
              <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ backgroundColor: activeProducts.includes(p.key) ? '#0A0E1A' : p.color }}></span>
              {p.key}
            </button>
          ))}
        </div>

        {/* Main Chart */}
        <div className="bg-[#161B27] rounded-2xl p-6 border border-[#1E2530]">
          <div className="mb-4">
            <h3 className="text-white font-bold text-base">제품별 매출 추이</h3>
            <p className="text-[#8B9BB4] text-xs mt-0.5">단위: 백만원</p>
          </div>
          <ResponsiveContainer width="100%" height={320}>
            {chartType === 'area' ? (
              <AreaChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
                <defs>
                  {PRODUCTS.map(p => (
                    <linearGradient key={p.key} id={`grad-${p.key}`} x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor={p.color} stopOpacity={0.3} />
                      <stop offset="95%" stopColor={p.color} stopOpacity={0} />
                    </linearGradient>
                  ))}
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1E2530" />
                <XAxis dataKey={xKey} tick={{ fill: '#8B9BB4', fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: '#8B9BB4', fontSize: 11 }} axisLine={false} tickLine={false} />
                <Tooltip content={<CustomTooltip />} />
                <Legend wrapperStyle={{ color: '#8B9BB4', fontSize: 12 }} />
                {PRODUCTS.map(p => activeProducts.includes(p.key) && (
                  <Area key={p.key} type="monotone" dataKey={p.key} stroke={p.color} fill={`url(#grad-${p.key})`} strokeWidth={2} dot={false} />
                ))}
              </AreaChart>
            ) : chartType === 'line' ? (
              <LineChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1E2530" />
                <XAxis dataKey={xKey} tick={{ fill: '#8B9BB4', fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: '#8B9BB4', fontSize: 11 }} axisLine={false} tickLine={false} />
                <Tooltip content={<CustomTooltip />} />
                <Legend wrapperStyle={{ color: '#8B9BB4', fontSize: 12 }} />
                {PRODUCTS.map(p => activeProducts.includes(p.key) && (
                  <Line key={p.key} type="monotone" dataKey={p.key} stroke={p.color} strokeWidth={2.5} dot={false} activeDot={{ r: 5, strokeWidth: 0 }} />
                ))}
              </LineChart>
            ) : (
              <BarChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1E2530" />
                <XAxis dataKey={xKey} tick={{ fill: '#8B9BB4', fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: '#8B9BB4', fontSize: 11 }} axisLine={false} tickLine={false} />
                <Tooltip content={<CustomTooltip />} />
                <Legend wrapperStyle={{ color: '#8B9BB4', fontSize: 12 }} />
                {PRODUCTS.map(p => activeProducts.includes(p.key) && (
                  <Bar key={p.key} dataKey={p.key} fill={p.color} radius={[4, 4, 0, 0]} />
                ))}
              </BarChart>
            )}
          </ResponsiveContainer>
        </div>

        {/* Product Performance Table */}
        <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] overflow-hidden">
          <div className="px-6 py-4 border-b border-[#1E2530]">
            <h3 className="text-white font-bold text-base">제품별 성과 요약</h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="bg-[#1E2530]">
                  <th className="text-left text-[#8B9BB4] text-xs font-semibold px-6 py-3">제품명</th>
                  <th className="text-right text-[#8B9BB4] text-xs font-semibold px-4 py-3">1월</th>
                  <th className="text-right text-[#8B9BB4] text-xs font-semibold px-4 py-3">6월</th>
                  <th className="text-right text-[#8B9BB4] text-xs font-semibold px-4 py-3">12월</th>
                  <th className="text-right text-[#8B9BB4] text-xs font-semibold px-4 py-3">연간 합계</th>
                  <th className="text-center text-[#8B9BB4] text-xs font-semibold px-4 py-3">성장률</th>
                  <th className="text-left text-[#8B9BB4] text-xs font-semibold px-4 py-3">추이</th>
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
                    <tr key={p.key} className={`border-t border-[#1E2530] hover:bg-[#00E5CC]/5 transition-colors ${idx % 2 === 1 ? 'bg-[#1A2035]/20' : ''}`}>
                      <td className="px-6 py-3">
                        <div className="flex items-center gap-2">
                          <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: p.color }}></span>
                          <span className="text-white text-sm font-semibold">{p.key}</span>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-[#8B9BB4] text-sm text-right">{jan.toLocaleString()}</td>
                      <td className="px-4 py-3 text-[#8B9BB4] text-sm text-right">{jun.toLocaleString()}</td>
                      <td className="px-4 py-3 text-white text-sm font-semibold text-right">{dec.toLocaleString()}</td>
                      <td className="px-4 py-3 text-[#00E5CC] text-sm font-bold text-right">{total.toLocaleString()}</td>
                      <td className="px-4 py-3 text-center">
                        <span className="text-emerald-400 text-xs font-semibold bg-emerald-400/10 px-2 py-1 rounded-full">+{growth}%</span>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-end gap-0.5 h-6">
                          {productSalesData.filter((_, i) => i % 2 === 0).map((row, i) => {
                            const val = (row as any)[p.key];
                            const maxVal = Math.max(...productSalesData.map(r => (r as any)[p.key]));
                            const height = Math.max(4, (val / maxVal) * 24);
                            return (
                              <div key={i} className="w-1.5 rounded-sm flex-shrink-0" style={{ height, backgroundColor: p.color + '80' }}></div>
                            );
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
