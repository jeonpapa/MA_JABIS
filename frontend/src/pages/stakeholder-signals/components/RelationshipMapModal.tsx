import { useEffect, useState } from 'react';
import { fetchRelationshipMapObjectUrl } from '@/api/stakeholder';

// 관계도 모달 — 인증 라우트의 SVG 를 fetch → blob → objectURL 로 <img> 에 표시.
// (<img src> 에 API 경로를 직접 넣으면 Authorization 헤더가 붙지 않는다.)

export default function RelationshipMapModal({ onClose }: { onClose: () => void }) {
  const [url, setUrl] = useState<string | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'empty' | 'error'>('loading');

  useEffect(() => {
    let cancelled = false;
    let created: string | null = null;
    fetchRelationshipMapObjectUrl()
      .then(u => {
        created = u;
        if (cancelled) return;
        setUrl(u);
        setState(u ? 'ready' : 'empty');
      })
      .catch(() => {
        if (!cancelled) setState('error');
      });
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-[60] bg-black/70 flex items-center justify-center p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="기관·인물 관계도"
    >
      <div
        className="bg-[#161B27] border border-[#1E2530] rounded-2xl w-full max-w-6xl max-h-[90vh] flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-3 border-b border-[#1E2530]">
          <div>
            <h2 className="text-white font-bold text-sm">기관·인물 관계도</h2>
            <p className="text-[#4A5568] text-[11px]">외부 에이전트가 발행한 정본 산출물(SVG) — 읽기 전용</p>
          </div>
          <button
            onClick={onClose}
            className="w-8 h-8 flex items-center justify-center rounded-lg text-[#8B9BB4] hover:text-white hover:bg-[#1E2530] cursor-pointer"
            aria-label="닫기"
          >
            <i className="ri-close-line text-lg"></i>
          </button>
        </div>
        <div className="flex-1 overflow-auto p-4 bg-white/95 rounded-b-2xl">
          {state === 'loading' && (
            <p className="text-center py-16 text-sm text-gray-500">
              <i className="ri-loader-4-line animate-spin mr-2"></i>관계도를 불러오는 중…
            </p>
          )}
          {state === 'empty' && <p className="text-center py-16 text-sm text-gray-500">아직 동기화된 관계도가 없습니다.</p>}
          {state === 'error' && <p className="text-center py-16 text-sm text-red-600">관계도를 불러오지 못했습니다.</p>}
          {state === 'ready' && url && <img src={url} alt="기관·인물 관계도" className="w-full h-auto mx-auto" />}
        </div>
      </div>
    </div>
  );
}
