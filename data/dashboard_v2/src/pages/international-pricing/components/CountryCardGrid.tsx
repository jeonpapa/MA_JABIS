import { useEffect, useState } from 'react';
import {
  fetchCountryOverview,
  type CountryOverviewCard,
  type CountryOverviewIndication,
  type CountryOverviewResponse,
  type ReimbursementSummary,
} from '../../../api/foreign';

interface Props {
  query: string;
}

const COUNTRY_FLAGS: Record<string, string> = {
  US: '🇺🇸', EU: '🇪🇺', UK: '🇬🇧', JP: '🇯🇵', AU: '🇦🇺', KR: '🇰🇷',
};

const COUNTRY_LABELS: Record<string, string> = {
  US: '미국', EU: '유럽', UK: '영국', JP: '일본', AU: '호주', KR: '한국',
};

const REIMB_PILL: Record<ReimbursementSummary, { label: string; cls: string }> = {
  recommend:      { label: '급여 권고',     cls: 'bg-emerald-500/15 text-emerald-300' },
  restrict:       { label: '조건부 급여',   cls: 'bg-amber-500/15 text-amber-300' },
  optimised:      { label: '제한 급여',     cls: 'bg-amber-500/15 text-amber-300' },
  reject:         { label: '비급여',        cls: 'bg-red-500/15 text-red-300' },
  not_listed:     { label: '미등재',        cls: 'bg-[#2A3545] text-[#8B9BB4]' },
  not_applicable: { label: '대상 외',       cls: 'bg-[#2A3545] text-[#8B9BB4]' },
  none:           { label: '정보 없음',     cls: 'bg-[#1E2530] text-[#4A5568]' },
};


function formatPrice(p: CountryOverviewIndication['price']): string {
  if (!p) return '-';
  const k = p.adjusted_price_krw;
  const local = p.local_price;
  if (k != null && p.currency && local != null) {
    return `${p.currency} ${local.toLocaleString()} ≈ ₩${Math.round(k).toLocaleString()}`;
  }
  if (k != null) return `₩${Math.round(k).toLocaleString()}`;
  if (local != null && p.currency) return `${p.currency} ${local.toLocaleString()}`;
  return '-';
}


function CountryCard({ card }: { card: CountryOverviewCard }) {
  const [open, setOpen] = useState(false);
  const flag = COUNTRY_FLAGS[card.country] ?? '🌐';
  const lab  = COUNTRY_LABELS[card.country] ?? card.country;
  const pill = REIMB_PILL[card.reimbursement_summary];

  return (
    <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full p-5 text-left hover:bg-[#1E2530]/30 transition-colors"
      >
        <div className="flex items-center justify-between gap-2 mb-3">
          <div className="flex items-center gap-2">
            <span className="text-2xl">{flag}</span>
            <div>
              <div className="text-white font-bold text-sm">{lab}</div>
              <div className="text-[#8B9BB4] text-[10px]">
                {card.agency || '—'}{card.body ? ` · ${card.body}` : ''}
              </div>
            </div>
          </div>
          <span className={`px-2 py-0.5 rounded text-[10px] font-semibold ${pill.cls}`}>
            {pill.label}
          </span>
        </div>
        <div className="grid grid-cols-2 gap-2 text-xs">
          <div>
            <div className="text-[#8B9BB4]">허가</div>
            <div className="text-white font-semibold">{card.approval_count}건</div>
          </div>
          <div>
            <div className="text-[#8B9BB4]">대표가격</div>
            <div className="text-white font-mono text-[11px]">{formatPrice(card.price_summary)}</div>
          </div>
        </div>
        <div className="mt-3 text-[10px] text-[#4A5568] flex items-center gap-1">
          <i className={`ri-arrow-${open ? 'up' : 'down'}-s-line`}></i>
          {open ? '닫기' : `적응증 ${card.indications.length}건 펼치기`}
        </div>
      </button>

      {open && card.indications.length > 0 && (
        <div className="border-t border-[#1E2530] divide-y divide-[#1E2530]/50">
          {card.indications.slice(0, 30).map((ind) => (
            <div key={ind.indication_id} className="p-3 text-[11px]">
              <div className="flex items-start justify-between gap-2 mb-1">
                <div className="text-white font-medium">{ind.title || ind.indication_id}</div>
                {ind.approval_date && (
                  <span className="text-[#8B9BB4] text-[10px] flex-shrink-0">
                    {ind.approval_date}
                  </span>
                )}
              </div>
              {ind.disease && (
                <div className="text-[#8B9BB4] text-[10px] mb-1">
                  {ind.disease}
                  {ind.line_of_therapy ? ` · ${ind.line_of_therapy}` : ''}
                  {ind.biomarker ? ` · ${ind.biomarker}` : ''}
                </div>
              )}
              {ind.reimbursement && (
                <div className="mt-1 px-2 py-1 rounded bg-[#1E2530] text-[10px]">
                  <span className="text-[#7FCEFF]">
                    {ind.reimbursement.body} {ind.reimbursement.decision_id || ''}
                  </span>
                  <span className="text-[#8B9BB4] ml-2">{ind.reimbursement.decision_type}</span>
                  {ind.reimbursement.criteria_text && (
                    <div className="text-[#8B9BB4] mt-1 line-clamp-2">
                      {ind.reimbursement.criteria_text}
                    </div>
                  )}
                </div>
              )}
              {ind.label_url && (
                <a href={ind.label_url} target="_blank" rel="noopener noreferrer"
                   className="text-[#00E5CC] hover:underline text-[10px] mt-1 inline-block">
                  허가 원문 ↗
                </a>
              )}
            </div>
          ))}
          {card.indications.length > 30 && (
            <div className="p-2 text-center text-[10px] text-[#4A5568]">
              + {card.indications.length - 30}건
            </div>
          )}
        </div>
      )}
    </div>
  );
}


export default function CountryCardGrid({ query }: Props) {
  const [data, setData] = useState<CountryOverviewResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!query.trim()) {
      setData(null);
      return;
    }
    setLoading(true);
    setError(null);
    fetchCountryOverview(query)
      .then((d) => setData(d))
      .catch((e) => setError(e?.message || String(e)))
      .finally(() => setLoading(false));
  }, [query]);

  if (loading) return <div className="text-[#8B9BB4] text-sm">로딩 중...</div>;
  if (error) return <div className="text-red-400 text-sm">오류: {error}</div>;
  if (!data) return null;

  return (
    <div className="space-y-4">
      <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-4 flex items-center justify-between">
        <div>
          <div className="text-white font-bold text-sm">{data.product}</div>
          {data.inn && <div className="text-[#8B9BB4] text-[11px]">INN: {data.inn}</div>}
        </div>
        <div className="text-[#8B9BB4] text-[11px]">
          {data.countries.length}개국 통합 view
        </div>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {data.countries.map((c) => (
          <CountryCard key={c.country} card={c} />
        ))}
      </div>
    </div>
  );
}
