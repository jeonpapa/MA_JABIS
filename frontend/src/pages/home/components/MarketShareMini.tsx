import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from 'recharts';
import { fetchMarketShareMini, formatQuarterKr, formatKrwCompact } from '@/api/home';
import { useApi } from '@/hooks/useApi';

interface Props {
  isDark?: boolean;
  /** 시장 anchor 검색어 (제품명/성분명). 기본 keytruda → PD-1/PD-L1 ATC4 시장 */
  query?: string;
}

const CustomTooltip = ({ active, payload, isDark }: any) => {
  if (active && payload && payload.length) {
    const bg = isDark ? 'bg-[#1E2530] border-[#2A3545]' : 'bg-white border-gray-200 shadow-lg';
    return (
      <div className={`${bg} rounded-xl p-3`}>
        <p className={`text-xs font-bold ${isDark ? 'text-white' : 'text-gray-900'}`}>{payload[0].name}</p>
        <p className={`text-sm font-bold ${isDark ? 'text-teal-400' : 'text-teal-600'}`}>{payload[0].value}%</p>
      </div>
    );
  }
  return null;
};

export default function MarketShareMini({ isDark = false, query = 'keytruda' }: Props) {
  const { data, loading, error } = useApi(() => fetchMarketShareMini(query), [query]);

  const slices = data?.slices ?? [];
  const total = slices.reduce((sum, d) => sum + d.value, 0);

  const cardBg = isDark ? 'bg-[#161B27]' : 'bg-white';
  const cardBorder = isDark ? 'border-[#1E2530]' : 'border-gray-200';
  const textMain = isDark ? 'text-white' : 'text-gray-900';
  const textSub = isDark ? 'text-[#8B9BB4]' : 'text-gray-500';
  const textMuted = isDark ? 'text-[#4A5568]' : 'text-gray-400';
  const barTrack = isDark ? 'bg-[#1E2530]' : 'bg-gray-200';

  return (
    <div className={`${cardBg} rounded-2xl p-6 border ${cardBorder} h-full`}>
      <div className="mb-4">
        <h3 className={`font-bold text-base ${textMain}`}>시장 점유율</h3>
        <p className={`${textSub} text-xs mt-0.5`}>
          {data ? `${formatQuarterKr(data.quarter) ?? '—'} 기준 · ${data.atc4Desc}` : '—'}
        </p>
      </div>
      {loading && (
        <div className="flex items-center justify-center" style={{ height: 200 }}>
          <p className={`${textMuted} text-xs`}>
            <i className={`ri-loader-4-line animate-spin mr-2 ${isDark ? 'text-[#00E5CC]' : 'text-teal-600'}`}></i>
            불러오는 중...
          </p>
        </div>
      )}
      {!loading && error && (
        <div className="flex items-center justify-center" style={{ height: 200 }}>
          <p className="text-red-500 text-xs text-center px-4">시장 점유율 조회 실패 — {error}</p>
        </div>
      )}
      {!loading && !error && slices.length === 0 && (
        <div className="flex items-center justify-center" style={{ height: 200 }}>
          <p className={`${textMuted} text-xs`}>시장 점유율 정보 없음</p>
        </div>
      )}
      {!loading && !error && slices.length > 0 && (
        <div className="flex flex-col items-center">
          <div className="relative w-full" style={{ height: 200 }}>
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={slices}
                  cx="50%"
                  cy="50%"
                  innerRadius={60}
                  outerRadius={90}
                  paddingAngle={3}
                  dataKey="value"
                >
                  {slices.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={entry.color} stroke="transparent" />
                  ))}
                </Pie>
                <Tooltip content={<CustomTooltip isDark={isDark} />} />
              </PieChart>
            </ResponsiveContainer>
            <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
              <p className={`text-xs ${textSub}`}>총 시장</p>
              <p className={`text-xl font-bold ${textMain}`}>{formatKrwCompact(data?.totalValuesLc ?? 0)}</p>
            </div>
          </div>
          <div className="w-full space-y-2 mt-2">
            {slices.map((item) => (
              <div key={item.name} className="flex items-center gap-2">
                <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: item.color }}></span>
                <span className={`text-xs flex-1 ${textSub}`}>{item.name}</span>
                <div className="flex items-center gap-2">
                  <div className={`w-16 h-1.5 rounded-full overflow-hidden ${barTrack}`}>
                    <div className="h-full rounded-full" style={{ width: `${total > 0 ? (item.value / total) * 100 : 0}%`, backgroundColor: item.color }}></div>
                  </div>
                  <span className={`text-xs font-semibold w-10 text-right ${textMain}`}>{item.value}%</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
