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
  isDark?: boolean;
}

const CustomTooltip = ({ active, payload, label, isDark }: any) => {
  if (active && payload && payload.length) {
    const d = payload[0]?.payload;
    const bg = isDark ? 'bg-[#1E2530] border-[#2A3545]' : 'bg-white border-gray-200 shadow-lg';
    const textMain = isDark ? 'text-white' : 'text-gray-900';
    const textSub = isDark ? 'text-[#8B9BB4]' : 'text-gray-500';
    return (
      <div className={`${bg} rounded-xl p-3 text-xs`}>
        <p className={`font-bold mb-1 ${textMain}`}>{label}</p>
        <p className={textSub}>상한금액: <span className={`font-semibold ${textMain}`}>₩{d?.price?.toLocaleString()}</span></p>
        <p className={textSub}>구분: <span className={textMain}>{d?.type}</span></p>
        {d?.changeRate !== null && d?.changeRate !== undefined && (
          <p className={textSub}>변동률: <span className={d.changeRate < 0 ? 'text-red-500 font-semibold' : 'text-emerald-500 font-semibold'}>
            {d.changeRate > 0 ? '+' : ''}{d.changeRate}%
          </span></p>
        )}
        <p className={textSub}>사유: <span className={textMain}>{d?.reason}</span></p>
      </div>
    );
  }
  return null;
};

export default function PriceWaterfall({ history, productName, isDark = false }: Props) {
  const data = history.map(h => ({ ...h, label: h.date.slice(0, 7) }));

  const cardBg = isDark ? 'bg-[#161B27]' : 'bg-white';
  const cardBorder = isDark ? 'border-[#1E2530]' : 'border-gray-200';
  const textMain = isDark ? 'text-white' : 'text-gray-900';
  const gridStroke = isDark ? '#1E2530' : '#E5E7EB';
  const tickFill = isDark ? '#8B9BB4' : '#6B7280';
  const refStroke = isDark ? '#4A5568' : '#9CA3AF';
  const legendText = isDark ? 'text-[#8B9BB4]' : 'text-gray-500';

  return (
    <div className={`rounded-2xl border p-5 ${cardBg} ${cardBorder}`}>
      <div className="flex items-center gap-2 mb-4">
        <span className={`w-5 h-5 flex items-center justify-center ${isDark ? 'text-[#00E5CC]' : 'text-teal-600'}`}>
          <i className="ri-bar-chart-2-line"></i>
        </span>
        <h3 className={`font-bold text-sm ${textMain}`}>약가 변동 이력 — {productName}</h3>
      </div>
      <ResponsiveContainer width="100%" height={200}>
        <ComposedChart data={data} margin={{ top: 10, right: 10, left: 10, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={gridStroke} vertical={false} />
          <XAxis dataKey="label" tick={{ fill: tickFill, fontSize: 10 }} axisLine={false} tickLine={false} />
          <YAxis tick={{ fill: tickFill, fontSize: 10 }} axisLine={false} tickLine={false}
            tickFormatter={v => `${(v / 10000).toFixed(0)}만`} domain={['auto', 'auto']} />
          <Tooltip content={<CustomTooltip isDark={isDark} />} />
          <ReferenceLine y={data[0]?.price} stroke={refStroke} strokeDasharray="4 4" />
          <Bar dataKey="price" radius={[4, 4, 0, 0]} maxBarSize={48}>
            {data.map((entry, index) => (
              <Cell key={index} fill={
                entry.type === '최초등재' ? '#0D9488' :
                entry.changeRate !== null && entry.changeRate < 0 ? '#EF4444' :
                entry.changeRate !== null && entry.changeRate > 0 ? '#10B981' : '#9CA3AF'
              } fillOpacity={0.85} />
            ))}
          </Bar>
        </ComposedChart>
      </ResponsiveContainer>
      <div className={`flex items-center gap-4 mt-2 text-xs ${legendText}`}>
        <div className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-sm bg-teal-600 flex-shrink-0"></span>최초등재</div>
        <div className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-sm bg-red-500 flex-shrink-0"></span>약가인하</div>
        <div className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-sm bg-emerald-500 flex-shrink-0"></span>약가인상</div>
        <div className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-sm bg-gray-400 flex-shrink-0"></span>유지</div>
      </div>
    </div>
  );
}