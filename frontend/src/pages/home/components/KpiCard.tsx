interface KpiCardProps {
  label: string;
  value: string;
  unit: string;
  change: string;
  changeType: 'up' | 'down' | 'neutral';
  description: string;
  accentColor?: string;
  isDark?: boolean;
}

export default function KpiCard({ label, value, unit, change, changeType, description, accentColor = '#0D9488', isDark = false }: KpiCardProps) {
  const changeColor = changeType === 'up' ? 'text-emerald-500 bg-emerald-50 border-emerald-200' : changeType === 'down' ? 'text-red-500 bg-red-50 border-red-200' : 'text-gray-500 bg-gray-50 border-gray-200';
  const changeIcon = changeType === 'up' ? 'ri-arrow-up-s-line' : changeType === 'down' ? 'ri-arrow-down-s-line' : 'ri-subtract-line';

  const cardBg = isDark ? 'bg-[#161B27]' : 'bg-white';
  const cardBorder = isDark ? 'border-[#1E2530]' : 'border-gray-200';
  const cardHover = isDark ? 'hover:border-[#2A3545]' : 'hover:border-gray-300';
  const textSub = isDark ? 'text-[#8B9BB4]' : 'text-gray-500';

  return (
    <div className={`${cardBg} rounded-2xl p-6 border ${cardBorder} ${cardHover} transition-all duration-200`}>
      <p className={`${textSub} text-xs font-semibold uppercase tracking-wider mb-3`}>{label}</p>
      <div className="flex items-end gap-2 mb-2">
        <span className="text-3xl font-bold" style={{ color: accentColor }}>{value}</span>
        {unit && <span className={`${textSub} text-sm mb-1`}>{unit}</span>}
        <span className={`ml-auto flex items-center gap-0.5 text-xs font-semibold px-2 py-1 rounded-full whitespace-nowrap border ${changeColor}`}>
          <i className={`${changeIcon} text-sm`}></i>
          {change}
        </span>
      </div>
      <p className={`${textSub} text-xs`}>{description}</p>
    </div>
  );
}