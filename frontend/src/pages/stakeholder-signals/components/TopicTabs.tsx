import { pill, pillCount } from '../theme/sv';

// 주제 탭 (전체 + 5주제) — 스케치 001 .tabs 알약형(활성 검정). 라벨은 호출자가 overview.topics(정본 MD 제목) 또는 TOPIC_LABELS 로 만든다.

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
    <div className="flex gap-1.5 overflow-x-auto p-0.5 -m-0.5" role="tablist" aria-label="주제">
      {tabs.map(t => {
        const active = value === t.slug;
        const n = counts?.[t.slug];
        return (
          <button
            key={t.slug}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(t.slug)}
            className={pill(active)}
          >
            {t.label}
            {typeof n === 'number' && <span className={pillCount(active)}>{n}</span>}
          </button>
        );
      })}
    </div>
  );
}
