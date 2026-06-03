import { useEffect, useState } from 'react';
import { api } from '@/api/client';

interface Props {
  open: boolean;
  onClose: () => void;
  /** 비교 카드에서 호출 — 약제명 prefill */
  initialBrandKey?: string;
  initialIsRsa?: 0 | 1 | null;
  initialRsaType?: string | null;
  initialRsaNote?: string | null;
  onSuccess?: () => void;
}

const RSA_TYPES = [
  { value: 'refund',          label: '환급형 (refund)' },
  { value: 'expenditure_cap', label: '총액제한형 (expenditure_cap)' },
  { value: 'utilization',     label: '사용량-약가 연동 (utilization)' },
  { value: 'conditional',     label: '조건부 급여 (conditional)' },
  { value: 'combined',        label: '복합 유형 (combined)' },
];

export default function RsaRegistryModal({
  open, onClose, initialBrandKey = '', initialIsRsa = 1,
  initialRsaType = 'refund', initialRsaNote = '', onSuccess,
}: Props) {
  const [brandKey, setBrandKey] = useState(initialBrandKey);
  const [isRsa, setIsRsa] = useState<0 | 1>(initialIsRsa === 0 ? 0 : 1);
  const [rsaType, setRsaType] = useState<string>(initialRsaType || 'refund');
  const [rsaNote, setRsaNote] = useState(initialRsaNote || '');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setBrandKey(initialBrandKey);
      setIsRsa(initialIsRsa === 0 ? 0 : 1);
      setRsaType(initialRsaType || 'refund');
      setRsaNote(initialRsaNote || '');
      setError(null);
    }
  }, [open, initialBrandKey, initialIsRsa, initialRsaType, initialRsaNote]);

  if (!open) return null;

  const submit = async () => {
    if (!brandKey.trim()) {
      setError('brand_key 필수 (예: "키트루다")');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await api.post('/api/admin/rsa-registry', {
        brand_key: brandKey.trim(),
        is_rsa: isRsa,
        rsa_type: isRsa === 1 ? rsaType : null,
        rsa_note: rsaNote.trim(),
        source: 'user_added (admin endpoint)',
      });
      onSuccess?.();
      onClose();
    } catch (e: any) {
      setError(e?.message || 'API 오류');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60" onClick={onClose}></div>
      <div className="relative bg-[#161B27] border border-[#2A3545] rounded-2xl w-full max-w-md mx-4 p-6 shadow-2xl">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-white font-bold text-base">RSA 등록 / 수정</h3>
          <button onClick={onClose} className="w-7 h-7 flex items-center justify-center text-[#8B9BB4] hover:text-white">
            <i className="ri-close-line text-lg"></i>
          </button>
        </div>
        <p className="text-[#8B9BB4] text-xs mb-4">
          위험분담제(RSA) 적용 약제 등록. 등록 시 모든 비교 카드와 사유 분석에 즉시 반영됩니다.
        </p>

        <div className="space-y-4">
          <div>
            <label className="block text-[#8B9BB4] text-xs mb-1">brand_key</label>
            <input
              type="text"
              value={brandKey}
              onChange={e => setBrandKey(e.target.value)}
              placeholder="키트루다"
              className="w-full bg-[#0D1117] border border-[#1E2530] rounded-lg px-3 py-2 text-white text-sm focus:border-[#00E5CC] focus:outline-none"
            />
            <p className="text-[10px] text-[#4A5568] mt-1">함량·제형 제거된 brand 핵심부 (예: '키트루다주' → '키트루다')</p>
          </div>

          <div>
            <label className="block text-[#8B9BB4] text-xs mb-1">RSA 적용</label>
            <div className="flex gap-2">
              <button
                onClick={() => setIsRsa(1)}
                className={`flex-1 py-2 rounded-lg text-sm font-medium ${isRsa === 1
                  ? 'bg-red-400/20 text-red-300 border border-red-400/40'
                  : 'bg-[#1E2530] text-[#8B9BB4] border border-transparent'}`}
              >
                대상 (1)
              </button>
              <button
                onClick={() => setIsRsa(0)}
                className={`flex-1 py-2 rounded-lg text-sm font-medium ${isRsa === 0
                  ? 'bg-emerald-400/20 text-emerald-300 border border-emerald-400/40'
                  : 'bg-[#1E2530] text-[#8B9BB4] border border-transparent'}`}
              >
                해당 없음 (0)
              </button>
            </div>
          </div>

          {isRsa === 1 && (
            <div>
              <label className="block text-[#8B9BB4] text-xs mb-1">유형</label>
              <select
                value={rsaType}
                onChange={e => setRsaType(e.target.value)}
                className="w-full bg-[#0D1117] border border-[#1E2530] rounded-lg px-3 py-2 text-white text-sm focus:border-[#00E5CC] focus:outline-none"
              >
                {RSA_TYPES.map(t => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </select>
            </div>
          )}

          <div>
            <label className="block text-[#8B9BB4] text-xs mb-1">메모 (선택)</label>
            <textarea
              value={rsaNote}
              onChange={e => setRsaNote(e.target.value)}
              placeholder="환급형. EGFR T790M+ NSCLC. 2018 보험 적용."
              rows={3}
              className="w-full bg-[#0D1117] border border-[#1E2530] rounded-lg px-3 py-2 text-white text-xs focus:border-[#00E5CC] focus:outline-none resize-none"
            />
          </div>

          {error && (
            <p className="text-red-400 text-xs">{error}</p>
          )}

          <button
            onClick={submit}
            disabled={submitting}
            className="w-full bg-[#00E5CC] text-[#0A0E1A] font-semibold text-sm py-2.5 rounded-xl disabled:opacity-50 hover:bg-[#00C9B1] transition-colors"
          >
            {submitting ? '등록 중...' : '등록'}
          </button>
        </div>
      </div>
    </div>
  );
}
