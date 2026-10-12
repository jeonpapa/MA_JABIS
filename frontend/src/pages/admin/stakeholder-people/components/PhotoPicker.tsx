import { useEffect, useRef, useState } from 'react';
import { PHOTO_ACCEPT, PHOTO_MAX_BYTES } from '@/api/stakeholder';
import PersonAvatar from '@/pages/stakeholder-signals/components/PersonAvatar';
import { btn, btnDanger, hint, linkBtn } from '@/pages/stakeholder-signals/theme/sv';

// 사진 선택 + 미리보기 (admin 전용). 미리보기는 로컬 File 의 objectURL 만 쓴다.
// 기존 사진은 인증 라우트(PersonAvatar)로만 그린다 — 언론 CDN photo_url 은 어떤 경우에도 <img> 로 쓰지 않는다.
// 서버가 640×640 WEBP 로 정규화(EXIF 회전·메타 제거)하므로 여기서는 형식·크기만 먼저 거른다.

export type PhotoIntent = { kind: 'keep' } | { kind: 'upload'; file: File } | { kind: 'remove' };

export default function PhotoPicker({
  personId,
  name,
  hasPhoto,
  photoVersion,
  value,
  onChange,
  disabled = false,
}: {
  personId?: string | null;
  name: string;
  hasPhoto: boolean;
  photoVersion: number;
  value: PhotoIntent;
  onChange: (v: PhotoIntent) => void;
  disabled?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (value.kind !== 'upload') {
      setPreview(null);
      return;
    }
    const url = URL.createObjectURL(value.file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [value]);

  const pick = (f: File | undefined) => {
    setErr(null);
    if (!f) return;
    if (!(PHOTO_ACCEPT as readonly string[]).includes(f.type)) {
      setErr('JPG, PNG, WEBP 파일만 업로드할 수 있습니다.');
      if (inputRef.current) inputRef.current.value = '';
      return;
    }
    if (f.size > PHOTO_MAX_BYTES) {
      setErr('파일 크기는 5MB 이하만 업로드할 수 있습니다.');
      if (inputRef.current) inputRef.current.value = '';
      return;
    }
    onChange({ kind: 'upload', file: f });
  };

  const showExisting = value.kind === 'keep' && hasPhoto;
  const canRemove = value.kind === 'upload' || showExisting;

  return (
    <div className="flex items-start gap-4">
      <div className="w-20 h-20 flex-shrink-0">
        {preview ? (
          <img
            src={preview}
            alt="업로드 사진 미리보기"
            className="w-20 h-20 rounded-full object-cover ring-1 ring-sv-avatar-ring"
          />
        ) : showExisting && personId ? (
          <span className="block [&>span]:!w-20 [&>span]:!h-20 [&>span]:!text-[23px]">
            <PersonAvatar key={`${personId}-${photoVersion}`} personId={personId} name={name} hasPhoto size="lg" />
          </span>
        ) : (
          <span className="w-20 h-20 rounded-full flex items-center justify-center bg-sv-avatar-bg ring-1 ring-sv-avatar-ring text-[23px] font-extrabold text-sv-avatar-fg">
            {Array.from(name.trim())[0] ?? '인'}
          </span>
        )}
      </div>
      <div className="min-w-0">
        <div className="flex flex-wrap gap-2">
          <label
            className={`${btn} focus-within:outline focus-within:outline-2 focus-within:outline-sv-focus ${disabled ? 'opacity-50 !cursor-not-allowed' : ''}`}
          >
            <i className="ri-image-add-line"></i>
            {hasPhoto || value.kind === 'upload' ? '사진 교체' : '사진 업로드'}
            <input
              ref={inputRef}
              type="file"
              accept={PHOTO_ACCEPT.join(',')}
              className="sr-only"
              disabled={disabled}
              onChange={e => pick(e.target.files?.[0])}
            />
          </label>
          {canRemove && (
            <button
              type="button"
              disabled={disabled}
              onClick={() => {
                setErr(null);
                if (inputRef.current) inputRef.current.value = '';
                onChange(value.kind === 'upload' ? { kind: 'keep' } : { kind: 'remove' });
              }}
              className={btnDanger}
            >
              <i className="ri-delete-bin-line"></i>
              {value.kind === 'upload' ? '선택 취소' : '사진 삭제'}
            </button>
          )}
          {value.kind === 'remove' && (
            <button
              type="button"
              onClick={() => onChange({ kind: 'keep' })}
              className={`text-[12px] ${linkBtn}`}
            >
              삭제 취소
            </button>
          )}
        </div>
        <p className={`mt-1.5 ${hint}`}>
          JPG·PNG·WEBP, 최대 5MB. 서버가 정사각형(640×640)으로 자동 크롭하고 WEBP 로 저장합니다(EXIF 등 메타데이터 제거).
          {value.kind === 'remove' && <span className="block text-sv-warn-fg font-semibold">저장하면 내부 사진이 삭제되고 이니셜로 표시됩니다.</span>}
          {value.kind === 'upload' && (
            <span className="block text-sv-accent-ink font-semibold">
              선택됨: {value.file.name} ({Math.round(value.file.size / 1024)}KB) — 저장 시 업로드
            </span>
          )}
        </p>
        {err && (
          <p role="alert" className="mt-1 text-[12px] font-semibold text-sv-bad-fg">
            {err}
          </p>
        )}
      </div>
    </div>
  );
}
