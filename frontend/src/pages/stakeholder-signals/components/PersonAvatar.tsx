import { useEffect, useState } from 'react';
import { fetchPersonPhotoObjectUrl } from '@/api/stakeholder';

// 인물 아바타 — 기본은 이니셜. 내부(admin 업로드) 사진이 있다고 서버가 알려줄 때만
// 인증 라우트(/api/stakeholder/people/<id>/photo)에서 blob 으로 받아 그린다.
// 언론 CDN 링크는 어떤 경우에도 쓰지 않는다(stakeholder_verbatim_rules.md §5).

// 스케치 .avatar 크기: 워크벤치 행 32 · 타임라인/인물 행 40 · 상세 56 · 드로어 프로필 72
const SIZE: Record<'sm' | 'md' | 'lg' | 'xl', string> = {
  sm: 'w-8 h-8 text-[11px]',
  md: 'w-10 h-10 text-[12px]',
  lg: 'w-14 h-14 text-[15px]',
  xl: 'w-[72px] h-[72px] text-[23px]',
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
  size?: 'sm' | 'md' | 'lg' | 'xl';
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
      className={`${SIZE[size]} flex-shrink-0 rounded-full overflow-hidden flex items-center justify-center bg-sv-avatar-bg text-sv-avatar-fg font-extrabold ring-1 ring-sv-avatar-ring`}
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
