import { useEffect, useState } from 'react';
import { fetchPersonPhotoObjectUrl } from '@/api/stakeholder';

// 인물 아바타 — 기본은 이니셜. 내부(admin 업로드) 사진이 있다고 서버가 알려줄 때만
// 인증 라우트(/api/stakeholder/people/<id>/photo)에서 blob 으로 받아 그린다.
// 언론 CDN 링크는 어떤 경우에도 쓰지 않는다(stakeholder_verbatim_rules.md §5).

const SIZE: Record<'sm' | 'md' | 'lg', string> = {
  sm: 'w-7 h-7 text-[11px]',
  md: 'w-9 h-9 text-xs',
  lg: 'w-12 h-12 text-sm',
};

function initialsOf(name?: string | null): string {
  const n = (name || '').trim();
  if (!n) return '?';
  return Array.from(n)[0];
}

export default function PersonAvatar({
  name,
  personId,
  hasPhoto,
  size = 'md',
}: {
  name?: string | null;
  personId?: string | null;
  hasPhoto?: boolean | null;
  size?: 'sm' | 'md' | 'lg';
}) {
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setSrc(null);
    if (personId && hasPhoto) {
      fetchPersonPhotoObjectUrl(personId).then(url => {
        if (!cancelled) setSrc(url);
      });
    }
    return () => {
      cancelled = true;
    };
  }, [personId, hasPhoto]);

  return (
    <span
      className={`${SIZE[size]} flex-shrink-0 rounded-full overflow-hidden flex items-center justify-center bg-[#1E2530] text-[#8B9BB4] font-bold border border-[#2A3545]`}
      aria-hidden={src ? undefined : true}
    >
      {src ? (
        <img src={src} alt={name || ''} className="w-full h-full object-cover" onError={() => setSrc(null)} />
      ) : (
        initialsOf(name)
      )}
    </span>
  );
}
