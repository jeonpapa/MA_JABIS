import {
  ComposedChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell, ReferenceLine,
} from 'recharts';

interface PriceHistory {
  date: string;
  price: number;
  type: string;
  reason: string;
  changeRate: number | null;
}

interface Props {
  history: PriceHistory[];
  productName: string;
}

const CustomTooltip = ({ active, payload, label }: any) => {
  if (active && payload && payload.length) {
    const d = payload[0]?.payload;
    return (
      <div className="bg-[#1E2530] border border-[#2A3545] rounded-xl p-3 shadow-xl text-xs">
        <p className="text-white font-bold mb-1">{label}</p>
        <p className="text-[#8B9BB4]">상한금액: <span className="text-white font-semibold">₩{d?.price?.toLocaleString()}</span></p>
        <p className="text-[#8B9BB4]">구분: <span className="text-white">{d?.type}</span></p>
        {d?.changeRate !== null && d?.changeRate !== undefined && (
          <p className="text-[#8B9BB4]">변동률: <span className={d.changeRate < 0 ? 'text-red-400 font-semibold' : 'text-emerald-400 font-semibold'}>
            {d.changeRate > 0 ? '+' : ''}{d.changeRate}%
          </span></p>
        )}
        <p className="text-[#8B9BB4]">사유: <span className="text-white">{d?.reason}</span></p>
      </div>
    );
  }
  return null;
};

function pickScale(maxPrice: number): { divisor: number; suffix: string; decimals: number } {
  if (maxPrice >= 100_000_000) return { divisor: 100_000_000, suffix: '억', decimals: 1 };
  if (maxPrice >= 10_000_000)  return { divisor: 10_000,       suffix: '만', decimals: 0 };
  if (maxPrice >= 1_000_000)   return { divisor: 10_000,       suffix: '만', decimals: 0 };
  if (maxPrice >= 100_000)     return { divisor: 10_000,       suffix: '만', decimals: 1 };
  if (maxPrice >= 10_000)      return { divisor: 1_000,        suffix: '천', decimals: 1 };
  return { divisor: 1, suffix: '원', decimals: 0 };
}

export default function PriceWaterfall({ history, productName }: Props) {
  const data = history.map(h => ({
    ...h,
    label: h.date.slice(0, 7),
  }));

  const maxPrice = data.reduce((m, d) => Math.max(m, d.price || 0), 0);
  const scale = pickScale(maxPrice);
  const formatY = (v: number) => {
    if (scale.suffix === '원') return v.toLocaleString();
    const scaled = v / scale.divisor;
    const rounded = scale.decimals === 0
      ? Math.round(scaled).toString()
      : scaled.toFixed(scale.decimals).replace(/\.?0+$/, '');
    return `${rounded}${scale.suffix}`;
  };

  return (
    <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5">
      <div className="flex items-center gap-2 mb-4">
        <span className="w-5 h-5 flex items-center justify-center">
          <i className="ri-bar-chart-2-line text-[#00E5CC]"></i>
        </span>
        <h3 className="text-white font-bold text-sm">약가 변동 이력 — {productName}</h3>
        <span className="ml-auto text-[10px] text-[#4A5568]">단위: {scale.suffix === '원' ? '원' : `${scale.suffix}원`}</span>
      </div>
      <ResponsiveContainer width="100%" height={200}>
        <ComposedChart data={data} margin={{ top: 10, right: 10, left: 20, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1E2530" vertical={false} />
          <XAxis dataKey="label" tick={{ fill: '#8B9BB4', fontSize: 10 }} axisLine={false} tickLine={false} />
          <YAxis
            tick={{ fill: '#8B9BB4', fontSize: 10 }}
            axisLine={false}
            tickLine={false}
            tickFormatter={formatY}
            width={64}
            domain={['auto', 'auto']}
          />
          <Tooltip content={<CustomTooltip />} />
          <ReferenceLine y={data[0]?.price} stroke="#4A5568" strokeDasharray="4 4" />
          <Bar dataKey="price" radius={[4, 4, 0, 0]} maxBarSize={48}>
            {data.map((entry, index) => (
              <Cell
                key={index}
                fill={
                  entry.type === '최초등재' ? '#00E5CC' :
                  entry.changeRate !== null && entry.changeRate < 0 ? '#EF4444' :
                  entry.changeRate !== null && entry.changeRate > 0 ? '#10B981' :
                  '#4A5568'
                }
                fillOpacity={0.85}
              />
            ))}
          </Bar>
        </ComposedChart>
      </ResponsiveContainer>
      <div className="flex items-center gap-4 mt-2 text-xs text-[#8B9BB4]">
        <div className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-sm bg-[#00E5CC] flex-shrink-0"></span>최초등재</div>
        <div className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-sm bg-red-500 flex-shrink-0"></span>약가인하</div>
        <div className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-sm bg-emerald-500 flex-shrink-0"></span>약가인상</div>
        <div className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-sm bg-[#4A5568] flex-shrink-0"></span>유지</div>
      </div>
    </div>
  );
}
