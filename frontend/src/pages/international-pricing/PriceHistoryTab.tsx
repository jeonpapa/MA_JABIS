import { useMemo, useState } from 'react';
import { useApi } from '@/hooks/useApi';
import { fetchForeignPriceHistory } from '@/api/foreign';
import type { PriceChangeKind, PriceHistory, PriceHistoryRow } from '@/api/foreign';
import { A8_CODES } from './ProductStrip';

/**
 * 변경 이력 — 국가 × 수집일 격자 (2026-11-03 사용자 승인 개선안).
 * 비교는 현지 통화 원가격. 원화 조정가는 환율·산식 변경이 섞이므로 쓰지 않는다.
 * 출처·가격 단계가 바뀐 칸(basis)은 가격 인하/인상으로 읽지 않도록 따로 표시한다.
 * 변경 유형 규칙은 서버 agents/db/foreign.py classify_price_sessions 와 같다 — 여기서는 선택 제형 행만으로 다시 계산한다.
 */

interface Cell {
  date: string; searchedAt: string; kind: PriceChangeKind; rows: PriceHistoryRow[];
  headline: number | null; currency: string | null; labels: string[]; prevLabels: string[]; prevHeadline: number | null;
  emailedAt: string | null; confirmed: boolean;
}

const SYM: Record<string, string> = { USD: '$', GBP: '£', EUR: '€', CHF: 'CHF ', JPY: '¥', CAD: 'CA$' };
const money = (v: number | null, cur: string | null) =>
  v == null ? '—' : `${SYM[cur || ''] ?? (cur ? cur + ' ' : '')}${v.toLocaleString('en-US', { minimumFractionDigits: cur === 'JPY' ? 0 : 2, maximumFractionDigits: cur === 'JPY' ? 0 : 2 })}`;
const normLabel = (s: string | null) => (s || '').toLowerCase().split(/\s+/).filter(Boolean).join(' ');
const mmdd = (d: string) => `${Number(d.slice(5, 7))}/${Number(d.slice(8, 10))}`;

function buildCells(sessions: PriceHistory['countries'][string], formKey: string | null): Cell[] | null {
  const anyMatch = !formKey || sessions.some(s => s.rows.some(r => r.formulation_key === formKey));
  if (!anyMatch) return null;
  // 같은 날 여러 세션이면 그날 마지막 세션만 격자에 쓴다(상세에는 그 세션 행).
  const byDate = new Map<string, { searchedAt: string; rows: PriceHistoryRow[] }>();
  for (const s of sessions) {
    const rows = formKey ? s.rows.filter(r => r.formulation_key === formKey) : s.rows;
    if (!rows.length) continue;
    const d = s.searched_at.slice(0, 10);
    const cur = byDate.get(d);
    if (!cur || s.searched_at > cur.searchedAt) byDate.set(d, { searchedAt: s.searched_at, rows });
  }
  const out: Cell[] = [];
  let prevSig: Map<string, string> | null = null;
  let prevLabels: string[] = [];
  let prevHeadline: number | null = null;
  for (const d of [...byDate.keys()].sort()) {
    const { searchedAt, rows } = byDate.get(d)!;
    const priced = rows.filter(r => r.local_price != null);
    const sig = new Map<string, string>();
    const groups = new Map<string, number[]>();
    for (const r of priced) {
      const k = r.formulation_key || '_';
      groups.set(k, [...(groups.get(k) || []), Math.round((r.local_price as number) * 100) / 100]);
    }
    for (const [k, v] of groups) sig.set(k, JSON.stringify([...v].sort((a, b) => a - b)));
    const labels = [...new Set(priced.map(r => normLabel(r.source_label)))].sort();
    const shownLabels = [...new Set(priced.map(r => r.source_label || ''))];
    let kind: PriceChangeKind;
    if (!priced.length) kind = 'unpriced';
    else if (!prevSig) kind = 'first';
    else {
      const common = [...sig.keys()].filter(k => prevSig!.has(k));
      const changed = common.filter(k => sig.get(k) !== prevSig!.get(k));
      const diff = [...sig.keys()].filter(k => !prevSig!.has(k)).length + [...prevSig.keys()].filter(k => !sig.has(k)).length;
      const sameLabels = JSON.stringify(labels) === JSON.stringify([...new Set(prevLabels.map(normLabel))].sort());
      kind = changed.length ? (sameLabels ? 'price' : 'basis') : diff ? 'packs' : sameLabels ? 'same' : 'relabel';
    }
    const units = priced.map(r => r.per_unit_local ?? r.local_price).filter((v): v is number => v != null && v > 0);
    const headline = units.length ? Math.min(...units) : null;
    const emailed = rows.flatMap(r => r.confirmed).find(c => c.emailed) || null;
    out.push({
      date: d, searchedAt, kind, rows, headline, currency: priced[0]?.currency ?? rows[0]?.currency ?? null,
      labels: shownLabels, prevLabels, prevHeadline,
      emailedAt: emailed ? (emailed.notified_at || emailed.captured_at) : null,
      confirmed: rows.some(r => r.confirmed.length > 0),
    });
    if (priced.length) { prevSig = sig; prevLabels = shownLabels; prevHeadline = headline; }
  }
  return out;
}

const KIND_TEXT: Record<PriceChangeKind, string> = {
  first: '최초 수집', same: '재확인 · 동일가', relabel: '가격 동일 · 출처 표기만 변경', packs: '포장·제형 구성 변경 (겹치는 포장 가격 동일)',
  basis: '출처·가격 단계 변경 — 같은 기준의 비교가 아니므로 인하·인상으로 읽지 않습니다', price: '같은 출처·단계에서 가격 변동',
  unpriced: '가격 없음 (수집 실패·미확정) — 비교에서 제외',
};

interface Props { query: string; formulationKey: string | null; formulationLabel: string | null; isDark: boolean; revision?: number }

export default function PriceHistoryTab({ query, formulationKey, formulationLabel, isDark, revision = 0 }: Props) {
  const hist = useApi<PriceHistory>(() => fetchForeignPriceHistory(query), [query, revision]);
  const [picked, setPicked] = useState<{ cc: string; date: string } | null>(null);

  const t = isDark
    ? { main: 'text-white', sub: 'text-[#8B9BB4]', muted: 'text-[#4A5568]', line: 'border-[#1E2530]', box: 'bg-[#161B27] border-[#1E2530]',
      basis: 'bg-amber-400/15', basisTag: 'bg-amber-400/20 text-amber-300', price: 'bg-rose-400/15', priceTag: 'bg-rose-400/20 text-rose-300', packsTag: 'bg-sky-400/15 text-sky-300', pick: 'ring-2 ring-[#00E5CC]/60' }
    : { main: 'text-gray-900', sub: 'text-gray-500', muted: 'text-gray-400', line: 'border-gray-200', box: 'bg-white border-gray-200',
      basis: 'bg-amber-50', basisTag: 'bg-amber-100 text-amber-800', price: 'bg-rose-50', priceTag: 'bg-rose-100 text-rose-700', packsTag: 'bg-sky-100 text-sky-700', pick: 'ring-2 ring-teal-400' };

  const grid = useMemo(() => {
    const countries = hist.data?.countries ?? {};
    const rows: { cc: string; cells: Cell[] | null }[] = [];
    const missing: string[] = [];
    const order = [...A8_CODES, ...Object.keys(countries).filter(c => !(A8_CODES as readonly string[]).includes(c)).sort()];
    for (const cc of order) {
      if (!countries[cc]?.length) { if ((A8_CODES as readonly string[]).includes(cc)) missing.push(cc); continue; }
      rows.push({ cc, cells: buildCells(countries[cc], formulationKey) });
    }
    const dates = [...new Set(rows.flatMap(r => (r.cells || []).map(c => c.date)))].sort();
    const counts = { basis: 0, price: 0 };
    for (const r of rows) { if ((r.cells || []).some(c => c.kind === 'basis')) counts.basis++; if ((r.cells || []).some(c => c.kind === 'price')) counts.price++; }
    return { rows, dates, missing, counts };
  }, [hist.data, formulationKey]);

  if (hist.loading && !hist.data) return <p className={`text-sm py-6 ${t.sub}`}>변경 이력 로딩 중…</p>;
  if (hist.error) return <p className="text-sm py-6 text-red-500">변경 이력 로딩 실패: {hist.error} <button className="underline" onClick={hist.reload}>다시 시도</button></p>;
  if (!grid.rows.length) return <p className={`text-sm py-6 ${t.sub}`}>저장된 수집 이력이 없습니다.</p>;

  const pickedCell = picked ? grid.rows.find(r => r.cc === picked.cc)?.cells?.find(c => c.date === picked.date) ?? null : null;

  return (
    <div className="space-y-3">
      <div className={`flex items-center gap-3 flex-wrap text-xs ${t.sub}`}>
        <span>{formulationLabel ? `제형 ${formulationLabel} · ` : ''}현지 통화, 포장당 가격을 단위(정·바이알)당으로 나눈 최저값</span>
        <span className="ml-auto flex items-center gap-2">
          <span className={`rounded px-1.5 ${t.priceTag}`} title="같은 기준에서 가격이 바뀐 국가 수">가격 변동 {grid.counts.price}개국</span>
          <span className={`rounded px-1.5 ${t.basisTag}`} title="출처·가격 단계가 바뀐 국가 수">기준·출처 변경 {grid.counts.basis}개국</span>
          <span className="inline-flex items-center gap-1"><i className="ri-mail-check-line"></i>결과 메일로 보낸 값</span>
        </span>
      </div>
      <div className={`overflow-x-auto rounded-xl border ${t.box}`}>
        <table className="min-w-full text-xs">
          <thead>
            <tr className={`border-b ${t.line}`}>
              <th className={`sticky left-0 z-[1] px-3 py-2 text-left font-mono font-normal ${isDark ? 'bg-[#161B27]' : 'bg-white'} ${t.muted}`}>국가</th>
              {grid.dates.map(d => <th key={d} className={`px-3 py-2 text-left font-mono font-normal whitespace-nowrap ${t.muted}`} title={d}>{mmdd(d)}</th>)}
            </tr>
          </thead>
          <tbody>
            {grid.rows.map(({ cc, cells }) => (
              <tr key={cc} className={`border-b last:border-0 ${t.line}`}>
                <th scope="row" className={`sticky left-0 z-[1] px-3 py-2 text-left font-mono font-normal ${isDark ? 'bg-[#161B27]' : 'bg-white'} ${t.sub}`}>{cc}</th>
                {cells == null
                  ? <td colSpan={grid.dates.length} className={`px-3 py-2 ${t.muted}`}>이 제형의 수집 기록 없음</td>
                  : grid.dates.map(d => {
                    const c = cells.find(x => x.date === d);
                    if (!c) return <td key={d} className={`px-3 py-2 ${t.muted}`} aria-label="수집 안 함">·</td>;
                    const isPick = picked?.cc === cc && picked.date === d;
                    const bg = c.kind === 'basis' ? t.basis : c.kind === 'price' ? t.price : '';
                    const pct = c.kind === 'price' && c.prevHeadline && c.headline ? ((c.headline - c.prevHeadline) / c.prevHeadline) * 100 : null;
                    const body = c.kind === 'same' ? <span className={t.muted}>= 동일</span>
                      : c.kind === 'relabel' ? <span className={t.muted}>= 동일 · 표기</span>
                        : c.kind === 'unpriced' ? <span className={t.sub}>미확정</span>
                          : <span className={`font-semibold tabular-nums ${t.main}`}>{money(c.headline, c.currency)}</span>;
                    return (
                      <td key={d} className={`p-0 ${bg}`}>
                        <button type="button" onClick={() => setPicked(isPick ? null : { cc, date: d })} aria-pressed={isPick}
                          title={KIND_TEXT[c.kind]}
                          className={`w-full text-left px-3 py-2 whitespace-nowrap cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-400 ${isPick ? t.pick : ''}`}>
                          {body}
                          {c.kind === 'basis' && <span className={`ml-1.5 rounded px-1 text-[10px] ${t.basisTag}`}>기준</span>}
                          {c.kind === 'price' && <span className={`ml-1.5 rounded px-1 text-[10px] ${t.priceTag}`}>{pct != null ? `${pct > 0 ? '+' : ''}${pct.toFixed(1)}%` : '변동'}</span>}
                          {c.kind === 'packs' && <span className={`ml-1.5 rounded px-1 text-[10px] ${t.packsTag}`}>포장</span>}
                          {c.emailedAt && <i className={`ri-mail-check-line ml-1.5 ${t.sub}`} title={`결과 메일로 보낸 값 · ${c.emailedAt.slice(0, 10)}`}></i>}
                        </button>
                      </td>
                    );
                  })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className={`rounded-xl border px-4 py-3 text-xs ${t.box}`} aria-live="polite">
        {!pickedCell ? (
          <p className={t.sub}>칸을 누르면 그 수집의 출처·포장별 가격이 보입니다. 노란 칸은 출처·가격 단계가 바뀐 것이고, 분홍 칸만 같은 기준에서의 실제 가격 변동입니다.</p>
        ) : (
          <div className="space-y-2">
            <p className={`font-semibold ${t.main}`}>{picked!.cc} · {pickedCell.searchedAt.slice(0, 16).replace('T', ' ')} · {KIND_TEXT[pickedCell.kind]}</p>
            {(pickedCell.kind === 'basis' || pickedCell.kind === 'relabel') && pickedCell.prevLabels.length > 0 && (
              <p className={t.sub}>출처·단계: {pickedCell.prevLabels.join(' / ')} → {pickedCell.labels.join(' / ')}</p>
            )}
            {pickedCell.kind === 'price' && <p className={t.sub}>단위당 최저가 {money(pickedCell.prevHeadline, pickedCell.currency)} → {money(pickedCell.headline, pickedCell.currency)}</p>}
            {pickedCell.emailedAt && <p className={t.sub}><i className="ri-mail-check-line"></i> 이 값은 조사 요청 결과로 확정되어 {pickedCell.emailedAt.slice(0, 10)} 메일로 안내되었습니다.</p>}
            <div className="overflow-x-auto">
              <table className="min-w-full">
                <thead><tr className={t.muted}><th className="text-left font-normal pr-4 py-1">포장</th><th className="text-right font-normal pr-4">포장 가격</th><th className="text-right font-normal pr-4">수량</th><th className="text-right font-normal pr-4">단위당</th><th className="text-left font-normal">출처</th></tr></thead>
                <tbody>
                  {pickedCell.rows.map(r => (
                    <tr key={r.id} className={t.main}>
                      <td className="pr-4 py-1 max-w-[280px] truncate" title={r.product_name || ''}>{r.dosage_strength || r.product_name || '—'}</td>
                      <td className="pr-4 text-right tabular-nums">{r.local_price == null ? '미확정' : money(r.local_price, r.currency)}</td>
                      <td className="pr-4 text-right tabular-nums">{r.pack_count ?? 1}</td>
                      <td className="pr-4 text-right tabular-nums">{money(r.per_unit_local, r.currency)}</td>
                      <td className={`${t.sub} max-w-[320px] truncate`} title={r.source_label || ''}>
                        {r.source_url ? <a href={r.source_url} target="_blank" rel="noreferrer" className="underline">{r.source_label || '출처'}</a> : (r.source_label || '—')}
                        {r.archived && <span className={`ml-1 ${t.muted}`}>(보관)</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
      <p className={`text-[11px] ${t.muted}`}>
        {grid.missing.length > 0 && <>수집 기록 없음: {grid.missing.join(', ')}. </>}
        2026-11-03 이전 일반 검색 경로는 과거 세션을 지웠으므로 그 이전 이력은 일부만 남아 있습니다. 원화 조정가는 환율·산식 변경이 섞여 이력 비교에 쓰지 않습니다.
      </p>
    </div>
  );
}
