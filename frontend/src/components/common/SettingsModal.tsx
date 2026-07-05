import { useEffect } from 'react';

/**
 * 범용 설정 편집 모달 — 헤더의 ⚙ 아이콘에서 열리는 중앙 정렬 패널.
 * 다크 테마 토큰은 competitor-trends 계열 페이지와 일치시킴 (#0D1117/#161B27/#1E2530).
 */
export default function SettingsModal({
  title,
  onClose,
  children,
  widthClassName = 'max-w-2xl',
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  widthClassName?: string;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/50 p-4 sm:p-8"
      onClick={onClose}
    >
      <div
        className={`w-full ${widthClassName} rounded-2xl bg-[#161B27] border border-[#1E2530] shadow-2xl`}
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-4 border-b border-[#1E2530] px-5 py-4">
          <h3 className="text-base font-bold text-white flex items-center gap-2">
            <i className="ri-settings-3-line text-[#00E5CC]"></i>
            {title}
          </h3>
          <button
            onClick={onClose}
            className="w-8 h-8 flex items-center justify-center rounded-lg text-[#8B9BB4] hover:bg-[#1E2530] hover:text-white cursor-pointer"
          >
            <i className="ri-close-line text-xl"></i>
          </button>
        </div>
        <div className="max-h-[75vh] overflow-y-auto p-5">{children}</div>
      </div>
    </div>
  );
}
