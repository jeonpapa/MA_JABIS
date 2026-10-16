import { useEffect, useId, useRef, useState } from 'react';
import {
  adminCreatePerson,
  adminDeletePhoto,
  adminUpdatePerson,
  adminUploadPhoto,
  ORG_FILTERS,
  svErrorMessage,
  type SVPersonDetail,
  type SVPersonInput,
} from '@/api/stakeholder';
import PhotoPicker, { type PhotoIntent } from './PhotoPicker';

// 스케치 003 인물 등록·수정 모달(.modal-wrap.open > .modal > .modal-head / form > .form / .modal-actions) 그대로.
//   - 등록은 항상 candidate(서버 강제). 인물 확인 상태 select 의 '검증됨' 은 스케치처럼 선택 불가.
//   - 검증 인물의 기관·부서·직책은 감사 제안 승인으로만 바뀐다 — 입력을 잠그고 .hint 로 알린다(서버도 409/422).
//   - 주제 태그 입력은 스케치에 없다: 등록은 서버 기본값, 수정은 기존 값을 그대로 보낸다(빼면 서버가 기본값으로 덮음).
//   - 저장 후 사진 의도(업로드/삭제)를 반영. 정보는 저장됐는데 사진만 실패하면 부모에 알린다.

const LIMITS = { name: [2, 30], organization: [2, 80], department: [1, 120], role: [1, 80] } as const;
const LABELS = { name: '이름', organization: '소속 기관', department: '부서', role: '직책' } as const;
type TextField = keyof typeof LIMITS;

function norm(s: string): string {
  return s.split(/\s+/).filter(Boolean).join(' ');
}

export interface SaveOutcome {
  person: SVPersonDetail;
  created: boolean;
  photoError?: string;
  photoChanged: boolean;
}

export default function PersonFormModal({
  person,
  photoVersion,
  onClose,
  onSaved,
}: {
  person: SVPersonDetail | null;
  photoVersion: number;
  onClose: () => void;
  onSaved: (o: SaveOutcome) => void;
}) {
  const editing = !!person;
  const verified = person?.verification_status === 'verified';
  const uid = useId();
  const nameRef = useRef<HTMLInputElement>(null);
  const [f, setF] = useState({
    name: person?.name ?? '',
    organization: person?.organization ?? '',
    department: person?.department ?? '',
    role: person?.role ?? '',
    status: (person?.status === 'paused' ? 'paused' : 'active') as 'active' | 'paused',
    aliases: (person?.aliases ?? []).join(', '),
  });
  const [verif, setVerif] = useState(verified ? 'verified' : 'candidate');
  const [photo, setPhoto] = useState<PhotoIntent>({ kind: 'keep' });
  const [fieldErr, setFieldErr] = useState<Partial<Record<TextField, string>>>({});
  const [formErr, setFormErr] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const t = window.setTimeout(() => nameRef.current?.focus(), 50);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !busy) onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.clearTimeout(t);
      window.removeEventListener('keydown', onKey);
    };
  }, [onClose, busy]);

  const locked = (k: TextField) => verified && (k === 'organization' || k === 'department' || k === 'role');

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormErr('');
    if (busy) return;
    if (!norm(f.name) || !norm(f.organization) || !norm(f.department) || !norm(f.role)) {
      setFormErr('이름, 소속 기관, 부서, 직책을 모두 입력해 주세요.');
      return;
    }
    const errs: Partial<Record<TextField, string>> = {};
    (Object.keys(LIMITS) as TextField[]).forEach(k => {
      const v = norm(f[k]);
      if (editing && v === (person?.[k] ?? '')) return; // 그대로인 기존 값은 길이 규칙 면제(서버와 같음)
      const [lo, hi] = LIMITS[k];
      if (v.length < lo || v.length > hi) errs[k] = `${LABELS[k]}은(는) ${lo}~${hi}자여야 합니다.`;
    });
    setFieldErr(errs);
    if (Object.keys(errs).length) return;

    const payload: SVPersonInput = {
      name: norm(f.name),
      organization: locked('organization') ? person!.organization ?? '' : norm(f.organization),
      department: locked('department') ? person!.department ?? '' : norm(f.department),
      role: locked('role') ? person!.role ?? '' : norm(f.role),
      status: f.status,
      aliases: f.aliases
        .split(',')
        .map(x => x.trim())
        .filter(Boolean),
    };
    if (editing) payload.topic_tags = person?.topic_tags ?? [];

    setBusy(true);
    let saved: SVPersonDetail;
    try {
      saved = editing ? await adminUpdatePerson(person!.person_id, payload) : await adminCreatePerson(payload);
    } catch (err) {
      setFormErr(svErrorMessage(err));
      setBusy(false);
      return;
    }
    const id = saved?.person_id ?? person?.person_id;
    let photoError: string | undefined;
    let photoChanged = false;
    if (id && photo.kind !== 'keep') {
      try {
        saved = photo.kind === 'upload' ? await adminUploadPhoto(id, photo.file) : await adminDeletePhoto(id);
        photoChanged = true;
      } catch (err) {
        photoError = svErrorMessage(err);
      }
    }
    setBusy(false);
    onSaved({ person: saved, created: !editing, photoError, photoChanged });
  };

  const field = (k: TextField, max: number, extra: { list?: string } = {}) => (
    <div className="field">
      <label htmlFor={`${uid}-${k}`}>{LABELS[k]} *</label>
      <input
        id={`${uid}-${k}`}
        ref={k === 'name' ? nameRef : undefined}
        value={f[k]}
        maxLength={max}
        required
        autoComplete="off"
        list={extra.list}
        disabled={locked(k) || busy}
        aria-invalid={!!fieldErr[k]}
        onChange={e => {
          const v = e.target.value;
          setF(s => ({ ...s, [k]: v }));
          if (fieldErr[k]) setFieldErr(m => ({ ...m, [k]: undefined }));
        }}
      />
      {k === 'role' && verified && <p className="hint">검증된 인물 — 기관·부서·직책은 직책 변경 감사 제안 승인으로만 바뀝니다.</p>}
      <div className="error">{fieldErr[k]}</div>
    </div>
  );

  return (
    <div className="modal-wrap open" role="dialog" aria-modal="true" aria-labelledby={`${uid}-title`}>
      <div className="modal">
        <div className="modal-head">
          <h2 id={`${uid}-title`}>{editing ? '인물 정보 수정' : '인물 등록'}</h2>
          <button onClick={onClose} disabled={busy} aria-label="등록창 닫기">
            ×
          </button>
        </div>
        <form onSubmit={submit} noValidate>
          <div className="form">
            <PhotoPicker
              personId={person?.person_id}
              name={f.name}
              hasPhoto={!!person?.has_photo}
              photoVersion={photoVersion}
              value={photo}
              onChange={setPhoto}
              disabled={busy}
            />
            <div className="notice" role="note">
              <b>익일 07:00 KST부터 반영</b>
              <br />
              활성으로 저장한 인물은 다음날 모니터링부터 계속 포함됩니다.
            </div>
            {field('name', 30)}
            {field('organization', 80, { list: `${uid}-orgs` })}
            <datalist id={`${uid}-orgs`}>
              {ORG_FILTERS.map(o => (
                <option key={o.key} value={o.label} />
              ))}
            </datalist>
            {field('department', 120)}
            {field('role', 80)}
            <div className="field">
              <label htmlFor={`${uid}-status`}>모니터링 상태 *</label>
              <select
                id={`${uid}-status`}
                value={f.status}
                disabled={busy}
                onChange={e => setF(s => ({ ...s, status: e.target.value as 'active' | 'paused' }))}
              >
                <option value="active">활성</option>
                <option value="paused">일시중지</option>
              </select>
              <p className="hint">활성 등록·정보 변경·일시중지는 모두 익일 모니터링부터 적용됩니다. 기존 수집 데이터는 유지됩니다.</p>
            </div>
            <div className="field">
              <label htmlFor={`${uid}-verif`}>인물 확인 상태</label>
              <select id={`${uid}-verif`} value={verif} onChange={e => setVerif(e.target.value)} disabled={busy}>
                <option value="candidate">후보 — 공식 직책 확인 전</option>
                <option value="verified" disabled>
                  검증됨 — 감사 근거 승인으로만 유지
                </option>
              </select>
            </div>
            <div className="field">
              <label htmlFor={`${uid}-aliases`}>
                다른 표기 <span className="muted">(선택)</span>
              </label>
              <input
                id={`${uid}-aliases`}
                value={f.aliases}
                placeholder="쉼표로 구분"
                disabled={busy}
                onChange={e => setF(s => ({ ...s, aliases: e.target.value }))}
              />
              <p className="hint">동명이인 구분 또는 기사에 쓰이는 다른 표기만 입력합니다.</p>
            </div>
            <div className="error" role="alert">
              {formErr}
            </div>
          </div>
          <div className="modal-actions">
            <button className="btn" type="button" onClick={onClose} disabled={busy}>
              취소
            </button>
            <button className="btn primary" type="submit" disabled={busy}>
              {busy ? '저장 중…' : editing ? '변경사항 저장' : '등록'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
