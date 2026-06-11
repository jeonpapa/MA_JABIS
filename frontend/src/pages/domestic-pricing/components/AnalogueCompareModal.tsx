interface Analogue {
  name: string;
  ingredient: string;
  price: number;
  dailyCost: number | null;
  company: string;
}

interface Props {
  open: boolean;
  onClose: () => void;
  baseProduct: { name: string; price: number; dailyCost: number | null };
  analogues: Analogue[];
  selected: string[];
  onToggle: (name: string) => void;
  isDark?: boolean;
}

export default function AnalogueCompareModal({ open, onClose, baseProduct, analogues, selected, onToggle, isDark = false }: Props) {
  if (!open) return null;

  const modalBg = isDark ? 'bg-[#161B27] border-[#2A3545]' : 'bg-white border-gray-200 shadow-2xl';
  const textMain = isDark ? 'text-white' : 'text-gray-900';
  const textSub = isDark ? 'text-[#8B9BB4]' : 'text-gray-500';
  const baseBg = isDark ? 'bg-[#00E5CC]/10 border-[#00E5CC]/30' : 'bg-teal-50 border-teal-300';
  const baseText = isDark ? 'text-[#00E5CC]' : 'text-teal-700';
  const itemBg = isDark ? 'bg-[#1E2530] border-[#1E2530]' : 'bg-gray-100 border-gray-200';
  const itemHover = isDark ? 'hover:border-[#2A3545]' : 'hover:border-gray-300';
  const itemSelectedBg = isDark ? 'bg-[#7C3AED]/10 border-[#7C3AED]/40' : 'bg-purple-50 border-purple-300';
  const itemDisabled = isDark ? 'bg-[#1E2530] border-[#1E2530] opacity-40 cursor-not-allowed' : 'bg-gray-100 border-gray-200 opacity-40 cursor-not-allowed';
  const checkSelected = isDark ? 'bg-[#7C3AED]' : 'bg-purple-600';
  const checkDefault = isDark ? 'border-[#4A5568]' : 'border-gray-400';
  const closeBtn = isDark ? 'text-[#8B9BB4] hover:text-white' : 'text-gray-500 hover:text-gray-900';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/50" onClick={onClose}></div>
      <div className={`relative rounded-2xl w-full max-w-lg mx-4 p-6 border ${modalBg}`}>
        <div className="flex items-center justify-between mb-4">
          <h3 className={`font-bold text-base ${textMain}`}>아날로그 약제 선택</h3>
          <button onClick={onClose} className={`w-7 h-7 flex items-center justify-center cursor-pointer transition-colors ${closeBtn}`}>
            <i className="ri-close-line text-lg"></i>
          </button>
        </div>
        <p className={`${textSub} text-xs mb-4`}>최대 2개까지 선택하여 비교할 수 있습니다 (기준 약제 포함 최대 3개)</p>

        <div className={`rounded-xl p-3 mb-3 ${baseBg}`}>
          <div className="flex items-center justify-between">
            <div>
              <p className={`text-xs font-semibold mb-0.5 ${baseText}`}>기준 약제</p>
              <p className={`text-sm font-bold ${textMain}`}>{baseProduct.name}</p>
            </div>
            <div className="text-right">
              <p className={`text-sm font-bold ${textMain}`}>₩{baseProduct.price.toLocaleString()}</p>
              {baseProduct.dailyCost && <p className={`text-xs ${textSub}`}>일치료비 ₩{baseProduct.dailyCost.toLocaleString()}</p>}
            </div>
          </div>
        </div>

        <div className="space-y-2">
          {analogues.map((a) => {
            const isSelected = selected.includes(a.name);
            const isDisabled = !isSelected && selected.length >= 2;
            return (
              <button key={a.name} onClick={() => !isDisabled && onToggle(a.name)}
                className={`w-full text-left rounded-xl p-3 border transition-all cursor-pointer ${isSelected ? itemSelectedBg : isDisabled ? itemDisabled : itemBg + ' ' + itemHover}`}>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <div className={`w-4 h-4 rounded flex items-center justify-center flex-shrink-0 ${isSelected ? checkSelected : 'border ' + checkDefault}`}>
                      {isSelected && <i className="ri-check-line text-white text-xs"></i>}
                    </div>
                    <div>
                      <p className={`text-sm font-medium ${textMain}`}>{a.name}</p>
                      <p className={`text-xs ${textSub}`}>{a.ingredient} · {a.company}</p>
                    </div>
                  </div>
                  <div className="text-right">
                    <p className={`text-sm font-semibold ${textMain}`}>₩{a.price.toLocaleString()}</p>
                    {a.dailyCost && <p className={`text-xs ${textSub}`}>일치료비 ₩{a.dailyCost.toLocaleString()}</p>}
                  </div>
                </div>
              </button>
            );
          })}
        </div>

        <button onClick={onClose}
          className="mt-4 w-full bg-teal-600 text-white font-semibold text-sm py-2.5 rounded-xl cursor-pointer hover:bg-teal-700 transition-colors whitespace-nowrap">
          선택 완료
        </button>
      </div>
    </div>
  );
}