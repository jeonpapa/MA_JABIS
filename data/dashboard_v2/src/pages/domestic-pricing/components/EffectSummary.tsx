import { useState } from 'react';

/**
 * 효능·효과 요약 표시.
 *
 * 식약처 허가정보(effect_text)는 `### 질병군` 헤더 + 번호 적응증 구조.
 * 키트루다처럼 적응증이 많은 약은 11,000자가 넘어 그대로 표시하면 가독성이 떨어진다.
 * → 질병군 단위로 접어서(collapsed) 보여주고, 클릭 시 세부 적응증을 펼친다.
 * 세부 용법·용량 원문은 맨 아래 토글로 분리.
 */

interface Props {
  /** 식약처 허가정보 효능·효과 원문 (mfds_permit.effect_text) */
  effectText: string | null;
  /** 용법·용량 원문 (drug_enrichment.usage_text) — 보조, 토글로 표시 */
  usageText: string | null;
}

interface Group {
  title: string;
  items: string[];
}

/** effect_text 를 질병군 그룹 + 도입 문장으로 파싱. */
function parseEffect(text: string): { intro: string[]; groups: Group[] } {
  const lines = text
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);
  const groups: Group[] = [];
  const intro: string[] = [];
  let cur: Group | null = null;

  for (const ln of lines) {
    const header = ln.match(/^#{1,4}\s*(.+)$/);
    if (header) {
      cur = { title: header[1].trim(), items: [] };
      groups.push(cur);
      continue;
    }
    const numbered = ln.match(/^(\d+)\s*[.)]\s*(.+)$/);
    const bullet = ln.match(/^[-•·∙▪‣]\s*(.+)$/);
    const body = numbered ? numbered[2] : bullet ? bullet[1] : ln;
    if (cur) cur.items.push(body);
    else intro.push(body);
  }
  return { intro, groups };
}

function GroupRow({ group }: { group: Group }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg bg-[#1A2030] border border-[#1E2530]">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-3 py-2 hover:bg-[#1E2530]/40 rounded-lg transition-colors"
      >
        <span className="flex items-center gap-2 min-w-0">
          <i className={`ri-arrow-${open ? 'down' : 'right'}-s-line text-[#4A5568] text-sm shrink-0`}></i>
          <span className="text-white text-xs font-semibold truncate">{group.title}</span>
        </span>
        <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-[#00E5CC]/10 text-[#00E5CC] shrink-0 ml-2">
          {group.items.length}건
        </span>
      </button>
      {open && (
        <ul className="px-3 pb-2.5 pt-0.5 space-y-1.5">
          {group.items.map((it, i) => (
            <li key={i} className="flex gap-2 text-[#8B9BB4] text-[11px] leading-snug">
              <span className="text-[#4A5568] shrink-0">{i + 1}.</span>
              <span>{it}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function EffectSummary({ effectText, usageText }: Props) {
  const [showUsage, setShowUsage] = useState(false);

  // 효능·효과가 없으면 기존 동작(용법·용량 원문) 으로 폴백
  if (!effectText || !effectText.trim()) {
    return usageText ? (
      <p className="text-[#8B9BB4] text-xs leading-relaxed whitespace-pre-wrap break-words">
        {usageText}
      </p>
    ) : (
      <p className="text-[#4A5568] text-xs">—</p>
    );
  }

  const { intro, groups } = parseEffect(effectText);

  return (
    <div className="space-y-3">
      {intro.length > 0 && (
        <div className="space-y-1">
          {intro.map((ln, i) => (
            <p key={i} className="text-[#8B9BB4] text-xs leading-relaxed">
              {ln}
            </p>
          ))}
        </div>
      )}

      {groups.length > 0 && (
        <div className="space-y-1.5">
          {groups.map((g, i) => (
            <GroupRow key={i} group={g} />
          ))}
        </div>
      )}

      {usageText && usageText.trim() && (
        <div className="pt-2 border-t border-[#1E2530]">
          <button
            type="button"
            onClick={() => setShowUsage((v) => !v)}
            className="text-[10px] text-[#4A5568] hover:text-[#8B9BB4] flex items-center gap-1 transition-colors"
          >
            <i className={`ri-arrow-${showUsage ? 'down' : 'right'}-s-line`}></i>
            용법·용량 원문 {showUsage ? '접기' : '보기'}
          </button>
          {showUsage && (
            <p className="text-[#8B9BB4] text-[11px] leading-relaxed whitespace-pre-wrap break-words mt-2">
              {usageText}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
