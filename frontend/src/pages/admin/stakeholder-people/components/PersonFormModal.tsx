import { useId, useState } from 'react';
import { Link } from 'react-router-dom';
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
import { btnGhost, btnPrimary, inputCls, Modal } from '@/pages/admin/stakeholder-audit/components/ui';
import PhotoPicker, { type PhotoIntent } from './PhotoPicker';

// 인물 등록·편집 모달 (스케치 003).
//   - 등록은 항상 candidate(서버가 강제). 확인 상태 select 의 '검증됨' 은 비활성 — 감사 근거 승인으로만.
//   - 검증 인물의 기관·부서·직책은 직접 수정하지 않는다(입력 비활성). 서버도 409/422 로 막고, 그 문구를 그대로 보여준다.
//   - 저장 후 사진 의도(업로드/삭제)를 반영. 정보 저장은 성공했는데 사진만 실패하면 부모에 알린다.

const LIMITS = { name: [2, 30], organization: [2, 80], department: [1, 120], role: [1, 80] } as const;
const LABELS = { name: '이름', organization: '소속 기관', department: '부서', role: '직책' } as const;
type TextField = keyof typeof LIMITS;

/** datalist 3기관 — Signals 화면 기관 필터와 같은 표시명. */
const ORG_OPTIONS = ORG_FILTERS.map(o => o.label);

function norm(s: string): string {
  return s.split(/\s+/).filter(Boolean).join(' ');
}

function splitList(s: string): string[] {
  return s
    .split(',')
    .map(x => x.trim())
    .filter(Boolean);
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
  const formId = useId();
  const [f, setF] = useState({
    name: person?.name ?? '',
    organization: person?.organization ?? '',
    department: person?.department ?? '',
    role: person?.role ?? '',
    status: (person?.status === 'paused' ? 'paused' : 'active') as 'active' | 'paused',
    aliases: (person?.aliases ?? []).join(', '),
    topic_tags: (person?.topic_tags ?? []).join(', '),
  });
  const [photo, setPhoto] = useState<PhotoIntent>({ kind: 'keep' });
  const [fieldErr, setFieldErr] = useState<Partial<Record<TextField, string>>>({});
  const [formErr, setFormErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const locked = (k: TextField) => verified && (k === 'organization' || k === 'department' || k === 'role');

  const validate = (): boolean => {
    const errs: Partial<Record<TextField, string>> = {};
    (Object.keys(LIMITS) as TextField[]).forEach(k => {
      const v = norm(f[k]);
      if (editing && v && v === (person?.[k] ?? '')) return; // 서버와 같이: 그대로인 기존 값은 길이 규칙 면제
      const [lo, hi] = LIMITS[k];
      if (v.length < lo || v.length > hi) errs[k] = `${LABELS[k]}은(는) ${lo}~${hi}자여야 합니다.`;
    });
    setFieldErr(errs);
    return Object.keys(errs).length === 0;
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormErr(null);
    if (busy || !validate()) return;
    const payload: SVPersonInput = {
      name: norm(f.name),
      organization: locked('organization') ? person!.organization ?? '' : norm(f.organization),
      department: locked('department') ? person!.department ?? '' : norm(f.department),
      role: locked('role') ? person!.role ?? '' : norm(f.role),
      status: f.status,
      aliases: splitList(f.aliases),
    };
    // 편집 시 topic_tags 를 빼면 서버가 기본값으로 덮는다 — 항상 현재 값을 보낸다. 등록은 비우면 서버 기본값.
    const tags = splitList(f.topic_tags);
    if (editing || tags.length) payload.topic_tags = tags;

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

  const field = (k: TextField, opts: { list?: string } = {}) => (
    <div>
      <label htmlFor={`${formId}-${k}`} className="block text-[#8B9BB4] text-[11px] font-semibold mb-1">
        {LABELS[k]} <span className="text-red-400">*</span>
        {locked(k) && <i className="ri-lock-line ml-1 text-[#4A5568]" title="검증 인물 — 감사 제안 승인으로만 변경"></i>}
      </label>
      <input
        id={`${formId}-${k}`}
        value={f[k]}
        onChange={e => {
          const v = e.target.value;
          setF(s => ({ ...s, [k]: v }));
          if (fieldErr[k]) setFieldErr(m => ({ ...m, [k]: undefined }));
        }}
        maxLength={LIMITS[k][1] + 20}
        list={opts.list}
        disabled={locked(k) || busy}
        aria-invalid={!!fieldErr[k]}
        className={inputCls}
        autoComplete="off"
      />
      {fieldErr[k] && <p className="mt-1 text-[11px] text-red-400">{fieldErr[k]}</p>}
    </div>
  );

  return (
    <Modal
      title={editing ? '인물 정보 수정' : '인물 등록'}
      onClose={onClose}
      busy={busy}
      footer={
        <>
          <button type="button" className={btnGhost} onClick={onClose} disabled={busy}>
            취소
          </button>
          <button type="submit" form={formId} className={btnPrimary} disabled={busy}>
            {busy ? '저장 중…' : editing ? '변경사항 저장' : '등록'}
          </button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} className="space-y-4" noValidate>
        <PhotoPicker
          personId={person?.person_id}
          name={f.name}
          hasPhoto={!!person?.has_photo}
          photoVersion={photoVersion}
          value={photo}
          onChange={setPhoto}
          disabled={busy}
        />

        <div className="rounded-lg bg-[#3B82F6]/10 px-3 py-2 text-[11px] leading-relaxed text-[#93C5FD]" role="note">
          <b>익일 07:00 KST 부터 반영</b> — 활성 등록·정보 변경·일시중지는 모두 다음날 모니터링부터 적용됩니다. 기존 수집
          데이터는 유지됩니다.
        </div>

        {verified && (
          <div className="rounded-lg border border-[#F59E0B]/30 bg-[#F59E0B]/5 px-3 py-2 text-[11px] leading-relaxed text-[#F59E0B]">
            검증된 인물입니다. 기관·부서·직책은 공식 근거가 있는 <b>직책 변경 감사 제안</b>을 승인해야 바뀝니다 —{' '}
            <Link to="/admin/stakeholder-audit" className="underline">
              모니터링 감사
            </Link>
            .
          </div>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {field('name')}
          <div>
            {field('organization', { list: `${formId}-orgs` })}
            <datalist id={`${formId}-orgs`}>
              {ORG_OPTIONS.map(o => (
                <option key={o} value={o} />
              ))}
            </datalist>
          </div>
          {field('department')}
          {field('role')}
          <div>
            <label htmlFor={`${formId}-status`} className="block text-[#8B9BB4] text-[11px] font-semibold mb-1">
              모니터링 상태 <span className="text-red-400">*</span>
            </label>
            <select
              id={`${formId}-status`}
              value={f.status}
              onChange={e => setF(s => ({ ...s, status: e.target.value as 'active' | 'paused' }))}
              className={`${inputCls} cursor-pointer`}
              disabled={busy}
            >
              <option value="active">활성</option>
              <option value="paused">일시중지</option>
            </select>
          </div>
          <div>
            <label htmlFor={`${formId}-verif`} className="block text-[#8B9BB4] text-[11px] font-semibold mb-1">
              인물 확인 상태
            </label>
            <select id={`${formId}-verif`} value={verified ? 'verified' : 'candidate'} disabled className={inputCls}>
              <option value="candidate">후보 — 공식 직책 확인 전</option>
              <option value="verified" disabled>
                검증됨 — 감사 근거 승인으로만
              </option>
            </select>
            <p className="mt-1 text-[10px] text-[#4A5568]">확인 상태는 직접 바꿀 수 없습니다(감사 근거 승인으로만 검증).</p>
          </div>
        </div>

        <div>
          <label htmlFor={`${formId}-aliases`} className="block text-[#8B9BB4] text-[11px] font-semibold mb-1">
            다른 표기 <span className="text-[#4A5568] font-normal">(선택, 쉼표로 구분)</span>
          </label>
          <input
            id={`${formId}-aliases`}
            value={f.aliases}
            onChange={e => setF(s => ({ ...s, aliases: e.target.value }))}
            className={inputCls}
            placeholder="동명이인 구분 또는 기사에 쓰이는 다른 표기"
            disabled={busy}
          />
        </div>
        <div>
          <label htmlFor={`${formId}-tags`} className="block text-[#8B9BB4] text-[11px] font-semibold mb-1">
            주제 태그 <span className="text-[#4A5568] font-normal">(쉼표로 구분{editing ? '' : ' · 비우면 기본 태그'})</span>
          </label>
          <input
            id={`${formId}-tags`}
            value={f.topic_tags}
            onChange={e => setF(s => ({ ...s, topic_tags: e.target.value }))}
            className={inputCls}
            disabled={busy}
          />
        </div>

        {formErr && (
          <p role="alert" className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300 whitespace-pre-wrap">
            {formErr}
          </p>
        )}
      </form>
    </Modal>
  );
}
