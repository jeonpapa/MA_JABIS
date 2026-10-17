import { Fragment, type ReactNode } from 'react';

/**
 * LLM 브리프용 최소 마크다운 렌더러 — '#', '**', '- ' 기호가 화면에 그대로 보이던 문제 대응.
 * 지원: # / ## / ### 제목, **굵게**, - · * 목록, 1. 번호 목록, 빈 줄 문단. HTML 은 해석하지 않는다(안전).
 */
function inline(text: string): ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((p, i) =>
    p.startsWith('**') && p.endsWith('**') && p.length > 4
      ? <strong key={i} className="font-semibold" style={{ color: 'var(--label)' }}>{p.slice(2, -2)}</strong>
      : <Fragment key={i}>{p}</Fragment>);
}

export default function MiniMarkdown({ text, className = '' }: { text: string; className?: string }) {
  const lines = (text || '').replace(/\r\n/g, '\n').split('\n');
  const out: ReactNode[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  const flush = () => {
    if (!list) return;
    const Tag = list.ordered ? 'ol' : 'ul';
    out.push(<Tag key={`l${out.length}`} className={`${list.ordered ? 'list-decimal' : 'list-disc'} pl-5 space-y-1 my-1.5`}>
      {list.items.map((it, i) => <li key={i}>{inline(it)}</li>)}</Tag>);
    list = null;
  };
  for (const raw of lines) {
    const line = raw.trimEnd();
    const h = line.match(/^(#{1,3})\s+(.*)$/);
    const ul = line.match(/^\s*[-*·]\s+(.*)$/);
    const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (h) {
      flush();
      const size = h[1].length === 1 ? 'text-[15px]' : 'text-[14px]';
      out.push(<p key={`h${out.length}`} className={`${size} font-semibold mt-3 mb-1`} style={{ color: 'var(--label)' }}>{inline(h[2])}</p>);
    } else if (ul || ol) {
      const ordered = !!ol;
      if (!list || list.ordered !== ordered) { flush(); list = { ordered, items: [] }; }
      list.items.push((ul ?? ol)![1]);
    } else if (!line.trim()) {
      flush();
    } else {
      flush();
      out.push(<p key={`p${out.length}`} className="my-1">{inline(line)}</p>);
    }
  }
  flush();
  return <div className={`leading-relaxed ${className}`}>{out}</div>;
}
