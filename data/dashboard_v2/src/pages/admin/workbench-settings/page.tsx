import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  fetchAssumptions,
  saveAssumptions,
  fetchDefaults,
  ALL_COUNTRIES,
  type Assumptions,
} from '@/api/workbench';

const COUNTRY_INFO: Record<string, { flag: string; name: string }> = {
  JP: { flag: '🇯🇵', name: 'Japan' },
  IT: { flag: '🇮🇹', name: 'Italy' },
  FR: { flag: '🇫🇷', name: 'France' },
  CH: { flag: '🇨🇭', name: 'Switzerland' },
  UK: { flag: '🇬🇧', name: 'United Kingdom' },
  DE: { flag: '🇩🇪', name: 'Germany' },
  US: { flag: '🇺🇸', name: 'United States' },
};

type Toast = { id: number; type: 'error' | 'success'; message: string };

export default function AdminWorkbenchSettingsPage() {
  const [assumptions, setAssumptions] = useState<Assumptions | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);

  useEffect(() => {
    fetchAssumptions().then(a => { setAssumptions(a); setDirty(false); }).catch(e => {
      showToast(`로드 실패: ${e.message}`, 'error');
    });
  }, []);

  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => { if (dirty) { e.preventDefault(); e.returnValue = ''; } };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  const showToast = (message: string, type: Toast['type'] = 'success') => {
    const id = Date.now() + Math.random();
    setToasts(t => [...t, { id, type, message }]);
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), 3000);
  };

  if (!assumptions) return <div className="min-h-screen bg-[#0D1117] text-[#8B9BB4] p-8">로딩…</div>;

  const updateField = (code: string, key: 'factory_ratio' | 'vat_rate' | 'margin_rate' | 'fx_rate_default', value: number) => {
    setAssumptions(prev => {
      if (!prev) return prev;
      const next = JSON.parse(JSON.stringify(prev)) as Assumptions;
      next.countries[code][key] = value;
      return next;
    });
    setDirty(true);
  };

  const togglePhase2 = (code: string, isPhase2: boolean) => {
    setAssumptions(prev => {
      if (!prev) return prev;
      const next = JSON.parse(JSON.stringify(prev)) as Assumptions;
      if (isPhase2) next.countries[code].phase = 2;
      else delete next.countries[code].phase;
      return next;
    });
    setDirty(true);
  };

  const updateGlobal = (k: 'fx_window_months' | 'fx_source', v: number | string) => {
    setAssumptions(prev => {
      if (!prev) return prev;
      return { ...prev, [k]: v } as Assumptions;
    });
    setDirty(true);
  };

  const onSave = async () => {
    if (!assumptions) return;
    setSaving(true);
    try {
      const body: Assumptions = {
        ...assumptions,
        last_updated: new Date().toISOString().slice(0, 10),
      };
      const r = await saveAssumptions(body);
      setAssumptions(r.saved);
      setDirty(false);
      showToast('저장 완료 · Audit Log 에 기록됨');
    } catch (e: any) {
      showToast(`저장 실패: ${e.message || e}`, 'error');
    } finally {
      setSaving(false);
    }
  };

  const onRestore = async () => {
    if (!confirm('HIRA 기본값으로 복원합니다. 현재 변경 내용은 사라집니다. 계속할까요?')) return;
    try {
      const d = await fetchDefaults();
      setAssumptions(JSON.parse(JSON.stringify(d)));
      setDirty(true);
      showToast('기본값 로드됨 · 저장 버튼을 눌러 확정하세요');
    } catch (e: any) {
      showToast(`기본값 로드 실패: ${e.message || e}`, 'error');
    }
  };

  return (
    <div className="min-h-screen bg-[#0D1117] text-white">
      <div className="px-8 pt-8 pb-6 border-b border-[#1E2530]">
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="w-5 h-5 flex items-center justify-center"><i className="ri-settings-3-line text-[#00E5CC]"></i></span>
              <h1 className="text-2xl font-bold">Workbench Settings — HIRA 가정치</h1>
            </div>
            <p className="text-[#8B9BB4] text-sm">공장도비율 · VAT · 유통마진 · 환율 기본값. 저장 시 A8 시나리오 계산에 즉시 반영.</p>
          </div>
          <div className="flex items-center gap-2">
            <Link to="/workbench" className="text-[#8B9BB4] hover:text-white text-sm">← Workbench</Link>
          </div>
        </div>
      </div>

      <div className="px-8 py-6 space-y-5">
        {/* Global */}
        <section className="bg-[#161B27] border border-[#1E2530] rounded-2xl p-5">
          <h2 className="text-base font-bold text-white mb-3">글로벌 설정</h2>
          <div className="grid grid-cols-4 gap-4">
            <div>
              <label className="text-xs text-[#8B9BB4] font-semibold block mb-1">환율 rolling window (개월)</label>
              <input
                type="number" value={assumptions.fx_window_months}
                onChange={e => updateGlobal('fx_window_months', Number(e.target.value))}
                className="w-full bg-[#0D1117] border border-[#1E2530] rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-[#00E5CC]/50"
              />
            </div>
            <div>
              <label className="text-xs text-[#8B9BB4] font-semibold block mb-1">환율 소스</label>
              <input
                type="text" value={assumptions.fx_source}
                onChange={e => updateGlobal('fx_source', e.target.value)}
                className="w-full bg-[#0D1117] border border-[#1E2530] rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-[#00E5CC]/50"
              />
            </div>
            <div>
              <label className="text-xs text-[#8B9BB4] font-semibold block mb-1">마지막 업데이트</label>
              <div className="bg-[#0D1117] border border-[#1E2530] rounded-lg px-3 py-2 text-[#8B9BB4] text-sm">{assumptions.last_updated || '—'}</div>
            </div>
            <div>
              <label className="text-xs text-[#8B9BB4] font-semibold block mb-1">수정자</label>
              <div className="bg-[#0D1117] border border-[#1E2530] rounded-lg px-3 py-2 text-[#8B9BB4] text-sm">{assumptions.updated_by || '—'}</div>
            </div>
          </div>
        </section>

        {/* Per-country */}
        <section>
          <h2 className="text-base font-bold text-white mb-3">국가별 가정치</h2>
          <div className="grid grid-cols-2 gap-4">
            {ALL_COUNTRIES.map(code => {
              const c = assumptions.countries[code];
              if (!c) return null;
              const info = COUNTRY_INFO[code] || { flag: '🏳', name: code };
              const isPhase2 = c.phase === 2;
              return (
                <div key={code} className={`rounded-2xl border p-4 ${isPhase2 ? 'bg-[#161B27]/50 border-[#1E2530] opacity-70' : 'bg-[#161B27] border-[#1E2530]'}`}>
                  <div className="flex items-center justify-between mb-3">
                    <h3 className="text-sm font-bold text-white flex items-center gap-2">
                      <span className="text-base">{info.flag}</span>{info.name}
                      <span className="text-[10px] bg-[#1E2530] text-[#8B9BB4] px-2 py-0.5 rounded-full">{c.currency}</span>
                    </h3>
                    {isPhase2 && <span className="text-[10px] bg-[#F59E0B]/20 text-[#F59E0B] px-2 py-0.5 rounded-full font-semibold">Phase 2</span>}
                  </div>

                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="text-[11px] text-[#8B9BB4] block mb-1">공장도비율 <span className="text-[#4A5568]">%</span></label>
                      <input
                        type="number" step="0.01" value={(c.factory_ratio * 100).toFixed(2)}
                        onChange={e => updateField(code, 'factory_ratio', Number(e.target.value) / 100)}
                        disabled={isPhase2}
                        className="w-full bg-[#0D1117] border border-[#1E2530] rounded-lg px-2 py-1.5 text-white text-xs font-mono focus:outline-none focus:border-[#00E5CC]/50 disabled:opacity-50"
                      />
                    </div>
                    <div>
                      <label className="text-[11px] text-[#8B9BB4] block mb-1">VAT <span className="text-[#4A5568]">%</span></label>
                      <input
                        type="number" step="0.01" value={(c.vat_rate * 100).toFixed(2)}
                        onChange={e => updateField(code, 'vat_rate', Number(e.target.value) / 100)}
                        disabled={isPhase2}
                        className="w-full bg-[#0D1117] border border-[#1E2530] rounded-lg px-2 py-1.5 text-white text-xs font-mono focus:outline-none focus:border-[#00E5CC]/50 disabled:opacity-50"
                      />
                    </div>
                    <div>
                      <label className="text-[11px] text-[#8B9BB4] block mb-1">유통마진 <span className="text-[#4A5568]">%</span></label>
                      <input
                        type="number" step="0.01" value={(c.margin_rate * 100).toFixed(2)}
                        onChange={e => updateField(code, 'margin_rate', Number(e.target.value) / 100)}
                        disabled={isPhase2}
                        className="w-full bg-[#0D1117] border border-[#1E2530] rounded-lg px-2 py-1.5 text-white text-xs font-mono focus:outline-none focus:border-[#00E5CC]/50 disabled:opacity-50"
                      />
                    </div>
                    <div>
                      <label className="text-[11px] text-[#8B9BB4] block mb-1">기본 환율 <span className="text-[#4A5568]">KRW/{c.currency}</span></label>
                      <input
                        type="number" step="0.01" value={c.fx_rate_default}
                        onChange={e => updateField(code, 'fx_rate_default', Number(e.target.value))}
                        disabled={isPhase2}
                        className="w-full bg-[#0D1117] border border-[#1E2530] rounded-lg px-2 py-1.5 text-white text-xs font-mono focus:outline-none focus:border-[#00E5CC]/50 disabled:opacity-50"
                      />
                    </div>
                  </div>

                  <label className="flex items-center gap-2 mt-3 text-xs text-[#8B9BB4] cursor-pointer">
                    <input
                      type="checkbox" checked={isPhase2}
                      onChange={e => togglePhase2(code, e.target.checked)}
                      className="w-4 h-4 accent-[#F59E0B]"
                    />
                    Phase 2 (계산 제외) — 데이터 수집 미구축 국가
                  </label>
                </div>
              );
            })}
          </div>
        </section>

        {/* Action bar */}
        <div className="sticky bottom-4 bg-[#161B27] border border-[#00E5CC]/30 rounded-2xl px-5 py-3 flex items-center gap-3 shadow-2xl">
          <div className="flex-1 text-sm">
            {dirty ? (
              <span className="text-[#F59E0B]">⚠ 저장하지 않은 변경사항</span>
            ) : (
              <span className="text-[#8B9BB4]">변경 없음</span>
            )}
          </div>
          <button
            onClick={onRestore}
            className="bg-[#1E2530] text-[#8B9BB4] hover:text-white text-xs font-semibold px-4 py-2 rounded-lg cursor-pointer transition-colors"
          >HIRA 기본값 복원</button>
          <button
            onClick={onSave}
            disabled={!dirty || saving}
            className="bg-[#00E5CC] text-[#0A0E1A] text-sm font-bold px-5 py-2 rounded-lg cursor-pointer hover:bg-[#00C9B1] transition-colors disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
          >{saving ? '저장 중…' : '💾 저장'}</button>
        </div>
      </div>

      {/* Toasts */}
      <div className="fixed bottom-24 right-6 space-y-2 z-50">
        {toasts.map(t => (
          <div
            key={t.id}
            className={`px-4 py-3 rounded-lg shadow-2xl text-xs max-w-sm border-l-4 bg-[#161B27] text-white ${
              t.type === 'success' ? 'border-[#22C55E]' : 'border-[#EF4444]'
            }`}
          >{t.message}</div>
        ))}
      </div>
    </div>
  );
}
