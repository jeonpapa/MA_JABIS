// 주제 탭 (전체 + 5주제). 라벨은 호출자가 overview.topics(정본 MD 제목) 또는 TOPIC_LABELS 로 만든다.

export default function TopicTabs({
  topics,
  value,
  counts,
  onChange,
}: {
  topics: { slug: string; label: string }[];
  value: string;
  counts?: Record<string, number>;
  onChange: (slug: string) => void;
}) {
  const tabs = [{ slug: 'all', label: '전체' }, ...topics];
  return (
    <div className="flex flex-wrap gap-1 border-b border-[#1E2530]" role="tablist" aria-label="주제">
      {tabs.map(t => {
        const active = value === t.slug;
        const n = counts?.[t.slug];
        return (
          <button
            key={t.slug}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(t.slug)}
            className={`-mb-px border-b-2 px-3.5 py-2 text-xs font-semibold transition-colors cursor-pointer whitespace-nowrap ${
              active
                ? 'border-[#00E5CC] text-[#00E5CC]'
                : 'border-transparent text-[#8B9BB4] hover:text-white'
            }`}
          >
            {t.label}
            {typeof n === 'number' && <span className="ml-1.5 text-[10px] text-[#4A5568]">{n}</span>}
          </button>
        );
      })}
    </div>
  );
}
