import { Fragment, useState } from 'react';
import Term from '@/components/ui/Term';
import type { DiffSeg, DrugItem, OncoRow } from '@/api/criteria';
import { GLOSS, copyRow, stepLabel } from './labels';

// 급여 기준 화면 공통 컴포넌트 — 쉬운 말 라벨·용어 설명·간결한 카드

export const T = ({ k, children }: { k: string; children: React.ReactNode }) => <Term def={GLOSS[k]}>{children}</Term>;

export function Badge({ children, tone = 'gray', title }: { children: React.ReactNode; tone?: 'gray' | 'teal' | 'amber' | 'blue' | 'violet' | 'red' | 'green'; title?: string }) {
  const cls = {
    gray: 'bg-gray-100 text-gray-600', teal: 'bg-teal-50 text-teal-700', amber: 'bg-amber-50 text-amber-800',
    blue: 'bg-sky-50 text-sky-700', violet: 'bg-violet-50 text-violet-700', red: 'bg-red-50 text-red-600', green: 'bg-emerald-50 text-emerald-700',
  }[tone];
  return <span title={title} className={`inline-flex items-center px-1.5 py-0.5 rounded text-[11px] font-medium ${cls}`}>{children}</span>;
}

/** 시행일이 오늘 이후면 '시행 예정' 배지 */
export function UpcomingBadge({ date }: { date?: string | null }) {
  if (!date || date <= new Date().toISOString().slice(0, 10)) return null;
  return <span className="ml-1 px-1 rounded bg-amber-100 text-amber-800 text-[10px] font-semibold" title="아직 시행 전(공고 확정, 시행일 이후 적용)">{date.slice(5)} 시행 예정</span>;
}

export function CopyButton({ text, label = '복사' }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button type="button" title="메일·메신저에 붙여 넣을 수 있게 복사"
      onClick={() => { navigator.clipboard?.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 1200); }); }}
      className="text-[11px] text-gray-400 hover:text-teal-700 whitespace-nowrap">
      <i className={done ? 'ri-check-line' : 'ri-file-copy-line'} /> {done ? '복사됨' : label}
    </button>
  );
}

function hl(text: string, kws: string[]) {
  const ks = kws.filter(k => k && k.length >= 2);
  if (!ks.length) return text;
  const re = new RegExp(`(${ks.map(k => k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'g');
  return text.split(re).map((p, i) => (ks.includes(p) ? <mark key={i} className="bg-yellow-100 rounded px-0.5">{p}</mark> : <Fragment key={i}>{p}</Fragment>));
}

/** 원문 텍스트: 파이프 표(| a | b |)는 표로, 나머지는 줄바꿈 유지. clampLines>0 이면 접기. */
export function RichText({ text, clampLines = 0, keywords = [] }: { text: string; clampLines?: number; keywords?: string[] }) {
  const [open, setOpen] = useState(false);
  if (!text?.trim()) return <span className="text-gray-300">—</span>;
  const blocks: ({ t: 'p'; s: string } | { t: 'tbl'; rows: string[][] })[] = [];
  for (const ln of text.split('\n')) {
    if (/^\s*\|.*\|\s*$/.test(ln)) {
      const cells = ln.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
      if (cells.every(c => /^:?-{3,}:?$/.test(c) || c === '')) continue;
      const last = blocks[blocks.length - 1];
      if (last && last.t === 'tbl') last.rows.push(cells); else blocks.push({ t: 'tbl', rows: [cells] });
    } else {
      const last = blocks[blocks.length - 1];
      if (last && last.t === 'p') last.s += '\n' + ln; else blocks.push({ t: 'p', s: ln });
    }
  }
  const body = (
    <div className="space-y-1.5">
      {blocks.map((b, i) => b.t === 'p'
        ? <div key={i} className="whitespace-pre-wrap leading-relaxed">{hl(b.s.trim(), keywords)}</div>
        : (
          <div key={i}>
          {b.rows.findIndex(r => r.some(c => c)) > 0 && (
            <div className="text-[11px] text-amber-700 mb-0.5"><i className="ri-error-warning-line" /> 이 표의 열 제목이 원문 변환 중 빠졌습니다. 정확한 조합은 원문에서 확인하세요.</div>
          )}
          <div className="overflow-x-auto">
          <table className="text-[11px] border border-gray-200">
            <tbody>
              {b.rows.filter(r => r.some(c => c)).map((r, j) => (
                <tr key={j} className={`border-b border-gray-100 last:border-0 ${j === 0 && b.rows.length > 1 ? 'bg-gray-50 font-semibold' : ''}`}>
                  {r.map((c, k) => <td key={k} className={`px-1.5 py-1 border-r border-gray-100 last:border-0 align-top ${k === 0 && j > 0 ? 'font-medium' : ''} ${c === '인정' ? 'text-emerald-700 text-center' : ''}`}>{c}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
          </div>
          </div>
        ))}
    </div>
  );
  if (!clampLines) return body;
  const long = text.length > clampLines * 45 || text.split('\n').length > clampLines;
  return (
    <div>
      <div className={open || !long ? '' : 'overflow-hidden'} style={open || !long ? undefined : { maxHeight: `${clampLines * 1.65}em` }}>{body}</div>
      {long && (
        <button type="button" onClick={() => setOpen(o => !o)} className="text-[11px] text-teal-600 hover:underline mt-0.5">
          {open ? '접기' : '전체 보기'}
        </button>
      )}
    </div>
  );
}

/** base 대비 diff. 초록=추가된 조건, 빨강 취소선(작게)=기준 약제에만 있는 조건. */
export function DiffText({ segs }: { segs: DiffSeg[] }) {
  return (
    <div className="whitespace-pre-wrap leading-relaxed">
      {segs.map((s, i) =>
        s.op === 'eq' ? <span key={i}>{s.text}</span>
          : s.op === 'add' ? <mark key={i} className="bg-emerald-100 text-emerald-900 rounded px-0.5">{s.text}</mark>
            : <del key={i} className="text-red-400 decoration-red-300 text-[11px]">{s.text}</del>)}
    </div>
  );
}

/** 항암 요법 한 건 — 간결 카드 */
export function RegimenCard({ r, keywords = [], id }: { r: OncoRow; keywords?: string[]; id?: string }) {
  return (
    <div id={id} className={`rounded-lg border p-3 text-xs bg-white ${r.focus ? 'border-teal-500 ring-1 ring-teal-200' : 'border-gray-200'}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="font-semibold text-teal-800 text-[13px]">{r.regimen}</div>
        <div className="flex items-center gap-2 shrink-0">
          {r.effective && r.effective > new Date().toISOString().slice(0, 10) && <Badge tone="amber" title="아직 시행 전(공고 확정, 시행일 이후 적용)">{r.effective.slice(5)} 시행 예정</Badge>}
          {r.notice && <span className="text-[11px] text-gray-400">{r.notice} · {r.effective}</span>}
          {r.source_url && <a href={r.source_url} className="text-[11px] text-teal-600 hover:underline" title={`${r.source_loc ?? '항암 공고 전문'} (PDF 내려받기 — 암종 절에서 연번으로 찾으세요)`}>원문</a>}
          <CopyButton text={copyRow(r)} />
        </div>
      </div>
      {(r.markers.length > 0 || r.self_pay) && (
        <div className="flex flex-wrap gap-1 mt-1">
          {r.markers.map(m => <Badge key={m.value} tone={r.marker_match?.includes(m.label) ? 'teal' : 'blue'}>{m.label}</Badge>)}
          {r.self_pay && <Badge tone="red" title="약값 전액 본인부담">전액 본인부담 {r.self_pay}</Badge>}
        </div>
      )}
      {r.is_combo_table ? (
        <div className="mt-1.5 space-y-1">
          <div><span className="text-gray-500">추가 약제 조건</span><RichText text={r.added_condition} clampLines={3} keywords={keywords} /></div>
          <div className="text-gray-700"><span className="text-gray-500">급여되는 기존 요법 · </span><b>{r.base_regimen}</b></div>
          <div className="text-gray-600"><RichText text={r.target} clampLines={2} keywords={keywords} /></div>
        </div>
      ) : (
        <div className="mt-1.5 text-gray-700"><RichText text={r.target} clampLines={3} keywords={keywords} /></div>
      )}
      {r.remark && <div className="mt-1 text-[11px] text-gray-500">{r.remark}</div>}
      {r.footnotes && r.footnotes.length > 0 && (
        <details className="mt-1.5 text-[11px] text-gray-500">
          <summary className="cursor-pointer hover:text-gray-700">각주 {r.footnotes.map(f => f.mark).join(', ')}</summary>
          <ul className="mt-1 space-y-0.5">{r.footnotes.map(f => <li key={f.mark}><b>{f.mark}</b> {f.text}</li>)}</ul>
        </details>
      )}
    </div>
  );
}

/** 치료 단계별 묶음 */
export function StepGroups({ rows, keywords = [], idPrefix = 'r' }: { rows: OncoRow[]; keywords?: string[]; idPrefix?: string }) {
  const groups: { label: string; rows: OncoRow[] }[] = [];
  for (const r of rows) {
    const label = stepLabel(r);
    const g = groups.find(x => x.label === label);
    if (g) g.rows.push(r); else groups.push({ label, rows: [r] });
  }
  return (
    <div className="space-y-4">
      {groups.map((g, i) => (
        <div key={i}>
          <div className="text-xs font-bold text-gray-700 mb-1.5 flex items-center gap-1">
            <i className="ri-arrow-right-s-fill text-teal-600" />{g.label}<span className="font-normal text-gray-400">· {g.rows.length}개</span>
          </div>
          <div className="grid gap-2">{g.rows.map(r => <RegimenCard key={r.key} r={r} keywords={keywords} id={`${idPrefix}-${r.key}`} />)}</div>
        </div>
      ))}
    </div>
  );
}

/** 한눈에 보기 — 치료 단계별 약제 이름만. 누르면 상세로 이동 */
export function AtAGlance({ rows, idPrefix = 'r', prefix }: { rows: OncoRow[]; idPrefix?: string; prefix?: (r: OncoRow) => string }) {
  const steps = new Map<string, OncoRow[]>();
  for (const r of rows) {
    const k = (prefix ? prefix(r) + ' · ' : '') + stepLabel(r);
    steps.set(k, [...(steps.get(k) ?? []), r]);
  }
  return (
    <div className="divide-y divide-gray-100">
      {[...steps.entries()].map(([label, rs]) => (
        <div key={label} className="flex gap-3 py-1.5 text-xs">
          <div className="w-48 shrink-0 font-semibold text-gray-700">{label}</div>
          <div className="flex flex-wrap gap-1">
            {rs.map(r => (
              <a key={r.key} href={`#${idPrefix}-${r.key}`}
                onClick={e => { e.preventDefault(); document.getElementById(`${idPrefix}-${r.key}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }); }}
                title={r.marker_match?.length ? `환자 조건과 맞음: ${r.marker_match.join(', ')}` : undefined}
                className={`px-1.5 py-0.5 rounded border ${r.focus ? 'bg-teal-600 text-white border-teal-600' : r.marker_match?.length ? 'border-teal-500 text-teal-800 bg-teal-50' : 'border-gray-200 text-gray-700 hover:border-teal-400'}`}>
                {r.marker_match?.length ? <i className="ri-focus-3-line mr-0.5" /> : null}{r.regimen}
                {r.effective && r.effective > new Date().toISOString().slice(0, 10) && <span className="ml-1 text-[10px] text-amber-700" title={`${r.effective} 시행 예정`}>●{r.effective.slice(5)}</span>}
              </a>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

/** 기준 본문에서 한 문단(제목 줄부터 다음 같은/상위 수준 제목 전까지) */
function sectionOf(text: string, heading: string): string {
  const lines = text.split('\n');
  const i = lines.findIndex(l => l.trim().startsWith(heading.slice(0, 30)));
  if (i < 0) return text;
  const lvl = (l: string) => (/^\s*\d{1,2}\.\s/.test(l) ? 1 : /^\s*[가-하]\.\s/.test(l) ? 2 : 0);
  const me = lvl(lines[i]) || 2;
  let j = i + 1;
  while (j < lines.length && !(lvl(lines[j]) && lvl(lines[j]) <= me)) j++;
  return lines.slice(i, j).join('\n');
}

export function GeneralItemCard({ item, keywords = [], id, ageLabel }: { item: DrugItem; keywords?: string[]; id?: string; ageLabel?: string }) {
  const [full, setFull] = useState(false);
  const [showKcd, setShowKcd] = useState(false);
  const [sec, setSec] = useState<string | null>(null);
  const toc = item.toc ?? [];
  const base = !full && item.excerpt ? item.excerpt : item.criteria;
  const text = full && sec ? sectionOf(item.criteria, sec) : base;
  return (
    <div id={id} className={`rounded-lg border bg-white p-3 text-xs ${item.focus ? 'border-teal-500 ring-1 ring-teal-200' : 'border-gray-200'}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="font-semibold text-gray-900 text-[13px]">{item.title}</div>
        <div className="flex items-center gap-2 shrink-0">
          <CopyButton text={`${item.title}\n${text}\n(${item.notice ?? ''}, ${item.effective ?? ''} 시행${item.url ? ` · ${item.url}` : ''})`} />
          {item.url && <a href={item.url} target="_blank" rel="noreferrer" className="text-[11px] text-teal-600 hover:underline" title={item.url_kind === '책자' ? '세부사항(약제) 책자 PDF' : '심평원 고시 상세'}>원문{item.url_kind === '책자' ? '(책자)' : ''} <i className="ri-external-link-line" /></a>}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-1 mt-1">
        {item.notice && <Badge tone="teal"><T k="고시">{item.notice}</T></Badge>}
        {item.effective && <Badge>{item.effective} 시행</Badge>}
        {item.latest_basis === 'gosi_newer' && <Badge tone="amber"><T k="최신반영">최신 고시 반영</T></Badge>}
        {item.match === 'mentioned' && <Badge tone="violet"><T k="묶음기준">묶음 기준</T></Badge>}
        {item.match === 'same_ingredient' && <Badge tone="gray">같은 성분 · 다른 품명</Badge>}
        {item.kcd.length > 0 && (
          <button type="button" onClick={() => setShowKcd(s => !s)} className="text-[11px] text-gray-400 hover:text-gray-600 ml-1">
            질병코드 {item.kcd.length}개 {showKcd ? '▴' : '▾'}
          </button>
        )}
      </div>
      {showKcd && (
        <div className="flex flex-wrap gap-1 mt-1">
          {item.kcd.map(k => <span key={k.kcd_code} className="px-1.5 py-0.5 rounded border border-gray-200 text-[11px] text-gray-600">{k.kcd_code} {k.name_kr}</span>)}
        </div>
      )}
      {item.age?.status === 'match' && item.age.text && (
        <div className="mt-2 rounded border border-amber-300 bg-amber-50 p-2">
          <div className="text-[11px] font-semibold text-amber-800 mb-0.5"><i className="ri-user-heart-line" /> {ageLabel ?? '입력한 나이'} 해당 조항 ({item.age.label})</div>
          <RichText text={item.age.text} keywords={keywords} />
        </div>
      )}
      {item.age?.status === 'outside' && <div className="mt-2 text-[11px] text-amber-800 bg-amber-50 rounded px-2 py-1"><i className="ri-user-unfollow-line" /> {item.age.message}</div>}
      {full && toc.length >= 4 && (
        <div className="mt-2 flex flex-wrap gap-1">
          <span className="text-[11px] text-gray-400 mr-1">목차</span>
          <button type="button" onClick={() => setSec(null)} className={`px-1.5 py-0.5 rounded border text-[11px] ${!sec ? 'bg-teal-600 text-white border-teal-600' : 'border-gray-200 text-gray-600'}`}>전체</button>
          {toc.map(h => (
            <button key={h} type="button" onClick={() => setSec(h)} title={h}
              className={`px-1.5 py-0.5 rounded border text-[11px] ${sec === h ? 'bg-teal-600 text-white border-teal-600' : 'border-gray-200 text-gray-600 hover:border-teal-400'}`}>{h.replace(/\s+/g, ' ').length > 40 ? h.replace(/\s+/g, ' ').slice(0, 38) + '…' : h.replace(/\s+/g, ' ')}</button>
          ))}
        </div>
      )}
      {item.excerpt && !full && <div className="mt-2 text-[11px] text-gray-400">{item.match === 'mentioned' ? '이 약이 언급된 부분' : '이 질환 관련 부분'}</div>}
      <div className="mt-1 text-gray-700"><RichText text={text} clampLines={full ? 0 : 8} keywords={keywords} /></div>
      {item.excerpt && (
        <button type="button" onClick={() => { setFull(f => !f); setSec(null); }} className="mt-1 text-[11px] text-teal-600 hover:underline">
          {full ? '관련 부분만 보기' : '기준 전체 보기'}
        </button>
      )}
      {!item.excerpt && toc.length >= 4 && !full && (
        <button type="button" onClick={() => setFull(true)} className="mt-1 text-[11px] text-teal-600 hover:underline">목차로 보기 ({toc.length}개 문단)</button>
      )}
    </div>
  );
}

export function Section({ title, count, note, children, defaultOpen = true }: {
  title: React.ReactNode; count?: number; note?: React.ReactNode; children: React.ReactNode; defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="mt-6">
      <button type="button" onClick={() => setOpen(o => !o)} className="flex items-center gap-2 mb-2 text-left w-full">
        <h3 className="font-bold text-gray-900 text-sm">{title}</h3>
        {count !== undefined && <span className="text-xs text-gray-400">{count}개</span>}
        <i className={`ri-arrow-${open ? 'up' : 'down'}-s-line text-gray-400 ml-auto`} />
      </button>
      {note && open && <p className="text-xs text-gray-500 mb-2">{note}</p>}
      {open && children}
    </section>
  );
}
