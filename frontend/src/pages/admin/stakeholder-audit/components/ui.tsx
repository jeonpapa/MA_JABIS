import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { REASON_MAX, REASON_MIN } from '@/api/stakeholder';
import type { ToastState } from './uiHelpers';

// 모니터링 인물 관리·모니터링 감사(admin) 공용 — 확인 모달, 사유 입력 모달, 토스트.
// 스케치는 확인·사유에 브라우저 confirm()/prompt() 를 쓴다. 대쉬보드는 스케치 003 모달 수치(common.css .sxm)로
// 같은 시각 언어의 모달을 띄운다(사유는 처리 이력에 남아야 해서 prompt 대신 입력 검증이 있는 모달).
// 버튼은 각 화면 스케치의 .btn / .btn.primary / .btn.danger 를 그대로 쓴다. 토스트는 각 화면 스케치의 .toast.

function ModalShell({
  title,
  onClose,
  busy,
  children,
  footer,
}: {
  title: string;
  onClose: () => void;
  busy: boolean;
  children: ReactNode;
  footer: ReactNode;
}) {
  const titleId = useId();
  const boxRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !busy) onClose();
    };
    window.addEventListener('keydown', onKey);
    const t = window.setTimeout(() => {
      boxRef.current?.querySelector<HTMLElement>('textarea:not([disabled]), button.primary, button.danger')?.focus();
    }, 30);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.clearTimeout(t);
    };
  }, [onClose, busy]);
  return (
    <div className="sxm-wrap" onClick={() => !busy && onClose()}>
      <div ref={boxRef} className="sxm" role="dialog" aria-modal="true" aria-labelledby={titleId} onClick={e => e.stopPropagation()}>
        <div className="sxm-head">
          <h2 id={titleId}>{title}</h2>
          <button onClick={onClose} disabled={busy} aria-label="닫기">
            ×
          </button>
        </div>
        <div className="sxm-body">{children}</div>
        <div className="sxm-actions">{footer}</div>
      </div>
    </div>
  );
}

/** 사유 입력 모달 — 승인·반려·충돌 확인. 사유 2~1000자. onSubmit 이 throw 하면 서버 문구를 모달 안에 그대로 보여준다. */
export function ReasonModal({
  title,
  description,
  confirmLabel,
  tone = 'primary',
  onSubmit,
  onClose,
}: {
  title: string;
  description?: ReactNode;
  confirmLabel: string;
  tone?: 'primary' | 'danger';
  onSubmit: (reason: string) => Promise<void>;
  onClose: () => void;
}) {
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const fieldId = useId();
  const len = reason.trim().length;
  const tooShort = len < REASON_MIN;
  const tooLong = len > REASON_MAX;

  const submit = async () => {
    if (tooShort || tooLong || busy) return;
    setBusy(true);
    setErr(null);
    try {
      await onSubmit(reason.trim());
    } catch (e) {
      setErr(e instanceof Error ? e.message : '요청을 처리하지 못했습니다.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <ModalShell
      title={title}
      onClose={onClose}
      busy={busy}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            취소
          </button>
          <button className={`btn ${tone}`} onClick={submit} disabled={tooShort || tooLong || busy}>
            {busy ? '처리 중…' : confirmLabel}
          </button>
        </>
      }
    >
      {description}
      <label htmlFor={fieldId}>
        사유 <span style={{ fontWeight: 400, color: '#777' }}>({REASON_MIN}~{REASON_MAX}자, 처리 이력에 기록됩니다)</span>
      </label>
      <textarea
        id={fieldId}
        value={reason}
        onChange={e => setReason(e.target.value)}
        maxLength={REASON_MAX + 50}
        rows={4}
        placeholder="판단 근거를 적어 주세요."
        onKeyDown={e => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void submit();
        }}
      />
      <div className="sxm-count">
        <span className={tooShort && len > 0 ? 'warn' : ''}>
          {tooShort ? `사유를 ${REASON_MIN}자 이상 입력해야 진행할 수 있습니다.` : tooLong ? `${REASON_MAX}자를 넘었습니다.` : ' '}
        </span>
        <span>
          {len}/{REASON_MAX}
        </span>
      </div>
      {err && (
        <div role="alert" className="svx-err">
          {err}
        </div>
      )}
    </ModalShell>
  );
}

export function ConfirmModal({
  title,
  body,
  confirmLabel,
  tone = 'primary',
  onConfirm,
  onClose,
}: {
  title: string;
  body: ReactNode;
  confirmLabel: string;
  tone?: 'primary' | 'danger';
  onConfirm: () => Promise<void>;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const go = async () => {
    setBusy(true);
    setErr(null);
    try {
      await onConfirm();
    } catch (e) {
      setErr(e instanceof Error ? e.message : '요청을 처리하지 못했습니다.');
    } finally {
      setBusy(false);
    }
  };
  return (
    <ModalShell
      title={title}
      onClose={onClose}
      busy={busy}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            취소
          </button>
          <button className={`btn ${tone}`} onClick={go} disabled={busy}>
            {busy ? '처리 중…' : confirmLabel}
          </button>
        </>
      }
    >
      <p>{body}</p>
      {err && (
        <div role="alert" className="svx-err">
          {err}
        </div>
      )}
    </ModalShell>
  );
}

/** 스케치 003/004 .toast(.show) — 마지막 문구를 유지해 사라지는 전환에서도 비지 않게. */
export function Toast({ toast }: { toast: ToastState | null }) {
  const last = useRef<ToastState | null>(null);
  if (toast) last.current = toast;
  const t = toast ?? last.current;
  return (
    <div className={`toast${toast ? ' show' : ''}${t?.tone === 'error' ? ' error' : ''}`} role="status" aria-live="polite">
      {t?.msg}
    </div>
  );
}
