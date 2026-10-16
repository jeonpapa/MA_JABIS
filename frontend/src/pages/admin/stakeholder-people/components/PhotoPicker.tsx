import { useEffect, useRef, useState } from 'react';
import { PHOTO_ACCEPT, PHOTO_MAX_BYTES } from '@/api/stakeholder';
import PersonAvatar from '@/pages/stakeholder-signals/components/PersonAvatar';

// 스케치 003 .photo-upload — 80px 아바타 미리보기 + "사진 업로드"(label.btn) / "사진 삭제"(btn danger) + 안내문.
// 미리보기는 로컬 File 의 objectURL, 기존 사진은 인증 라우트(PersonAvatar)로만 — 언론 CDN photo_url 은 쓰지 않는다.
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
  const inputId = `photo-${personId ?? 'new'}`;

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

  const showExisting = value.kind === 'keep' && hasPhoto && !!personId;
  const hasSrc = !!preview || showExisting;
  const initial = Array.from(name.trim())[0] ?? '인';

  return (
    <div className="photo-upload">
      {preview ? (
        <span className="avatar">
          <img src={preview} alt="업로드 사진 미리보기" />
        </span>
      ) : showExisting ? (
        <PersonAvatar key={`${personId}-${photoVersion}`} personId={personId} name={name} hasPhoto className="avatar" />
      ) : (
        <span className="avatar">{initial}</span>
      )}
      <div>
        <div className="photo-actions">
          <label className="btn" htmlFor={inputId} aria-disabled={disabled || undefined}>
            사진 업로드
          </label>
          <input
            ref={inputRef}
            id={inputId}
            type="file"
            accept={PHOTO_ACCEPT.join(',')}
            disabled={disabled}
            onChange={e => pick(e.target.files?.[0])}
          />
          {hasSrc && (
            <button
              className="btn danger"
              type="button"
              disabled={disabled}
              onClick={() => {
                setErr(null);
                if (inputRef.current) inputRef.current.value = '';
                onChange(value.kind === 'upload' && !hasPhoto ? { kind: 'keep' } : { kind: 'remove' });
              }}
            >
              사진 삭제
            </button>
          )}
        </div>
        <p className="hint">JPG·PNG·WEBP, 최대 5MB. 정사각형으로 자동 크롭됩니다.</p>
        {err && (
          <div className="error" role="alert">
            {err}
          </div>
        )}
      </div>
    </div>
  );
}
