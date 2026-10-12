import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { REASON_MAX, REASON_MIN } from '@/api/stakeholder';
import { boxCard, closeBtn, errorBox, input as inputCls, scrimStrong } from '@/pages/stakeholder-signals/theme/sv';
import { btnDanger, btnGhost, btnPrimary, type ToastState } from './uiHelpers';

// 모니터링 인물 관리·모니터링 감사(admin) 공용 UI — 모달 셸, 사유 입력 모달(prompt 금지), 확인 모달, 토스트.
// 스타일은 원 레포 스케치 003/004 의 화이트 톤(.btn/.btn.primary/.field/.modal/.toast) — theme/sv.ts 토큰만 쓴다.
// 이 컴포넌트들은 .sv-light 래퍼(SvPage) 안에서만 렌더링된다.


/** 기본 모달 셸: Esc·배경 클릭 닫기, aria-modal, 열릴 때 첫 입력 포커스. */
export function Modal({
  title,
  onClose,
  children,
  footer,
  wide = false,
  busy = false,
}: {
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
  busy?: boolean;
}) {
  const titleId = useId();
  const boxRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !busy) onClose();
    };
    window.addEventListener('keydown', onKey);
    const t = window.setTimeout(() => {
      const el = boxRef.current?.querySelector<HTMLElement>(
        'textarea:not([disabled]), input:not([disabled]):not([type=file]), select:not([disabled])',
      );
      el?.focus();
    }, 30);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.clearTimeout(t);
    };
  }, [onClose, busy]);
  return (
    <div
      className={`fixed inset-0 z-[60] ${scrimStrong} flex items-stretch sm:items-center justify-center p-0 sm:p-5`}
      onClick={() => !busy && onClose()}
    >
      <div
        ref={boxRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={`bg-sv-surface sm:rounded-xl shadow-sv-modal w-full ${wide ? 'sm:max-w-4xl' : 'sm:max-w-[580px]'} sm:max-h-[90vh] flex flex-col overflow-hidden`}
        onClick={e => e.stopPropagation()}
      >
        <div className="flex-shrink-0 flex items-center justify-between gap-3 px-[22px] py-4 border-b border-sv-line-soft">
          <h2 id={titleId} className="text-sv-ink text-[18px] font-extrabold truncate">
            {title}
          </h2>
          <button onClick={onClose} disabled={busy} aria-label="닫기" className={closeBtn}>
            <i className="ri-close-line text-lg"></i>
          </button>
        </div>
        <div className="flex-1 min-h-0 px-[22px] py-5 overflow-y-auto">{children}</div>
        {footer && (
          <div className="flex-shrink-0 px-[22px] py-4 border-t border-sv-line-soft flex flex-wrap justify-end gap-2">{footer}</div>
        )}
      </div>
    </div>
  );
}

/**
 * 사유 입력 모달 — 승인·반려·충돌 확인 공용. 사유 2~1000자(서버 REASON_MIN/MAX)를 클라이언트에서 먼저 막는다.
 * onSubmit 이 throw 하면 서버 문구를 모달 안에 그대로 보여주고 열어 둔다(사유 보존).
 */
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
    <Modal
      title={title}
      onClose={onClose}
      busy={busy}
      footer={
        <>
          <button className={btnGhost} onClick={onClose} disabled={busy}>
            취소
          </button>
          <button
            className={tone === 'danger' ? btnDanger : btnPrimary}
            onClick={submit}
            disabled={tooShort || tooLong || busy}
          >
            {busy ? '처리 중…' : confirmLabel}
          </button>
        </>
      }
    >
      {description && <div className="mb-4 text-[13px] text-sv-text leading-relaxed">{description}</div>}
      <label htmlFor={fieldId} className="block text-sv-ink text-[12px] font-bold mb-1.5">
        사유 <span className="text-sv-muted font-normal">({REASON_MIN}~{REASON_MAX}자, 처리 이력에 기록됩니다)</span>
      </label>
      <textarea
        id={fieldId}
        value={reason}
        onChange={e => setReason(e.target.value)}
        maxLength={REASON_MAX + 50}
        rows={4}
        className={`${inputCls} resize-y`}
        placeholder="판단 근거를 적어 주세요."
        onKeyDown={e => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void submit();
        }}
      />
      <div className="mt-1 flex justify-between text-[11px]">
        <span className={tooShort && len > 0 ? 'text-sv-warn-fg font-semibold' : 'text-sv-muted'}>
          {tooShort ? `사유를 ${REASON_MIN}자 이상 입력해야 진행할 수 있습니다.` : tooLong ? `${REASON_MAX}자를 넘었습니다.` : ' '}
        </span>
        <span className={tooLong ? 'text-sv-bad-fg font-semibold' : 'text-sv-muted'}>
          {len}/{REASON_MAX}
        </span>
      </div>
      {err && (
        <p role="alert" className={`mt-3 whitespace-pre-wrap ${errorBox}`}>
          {err}
        </p>
      )}
    </Modal>
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
    <Modal
      title={title}
      onClose={onClose}
      busy={busy}
      footer={
        <>
          <button className={btnGhost} onClick={onClose} disabled={busy}>
            취소
          </button>
          <button className={tone === 'danger' ? btnDanger : btnPrimary} onClick={go} disabled={busy}>
            {busy ? '처리 중…' : confirmLabel}
          </button>
        </>
      }
    >
      <div className="text-[13px] text-sv-ink-2 leading-relaxed">{body}</div>
      {err && (
        <p role="alert" className={`mt-3 ${errorBox}`}>
          {err}
        </p>
      )}
    </Modal>
  );
}

export function Toast({ toast }: { toast: ToastState | null }) {
  if (!toast) return null;
  return (
    <div
      role="status"
      aria-live="polite"
      className={`fixed bottom-6 left-1/2 -translate-x-1/2 z-[70] max-w-[90vw] rounded-lg px-4 py-3 text-[13px] font-semibold shadow-sv-toast ${
        toast.tone === 'error' ? 'bg-sv-bad-fg text-sv-on-dark' : 'bg-sv-dark text-sv-on-dark'
      }`}
    >
      {toast.msg}
    </div>
  );
}

/** 스케치 004 .stat — 테두리 카드, 12px 라벨 + 19px 값. tone 은 값 글자색 클래스(sv 토큰). */
export function StatCard({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: string }) {
  return (
    <div className={`${boxCard} px-4 py-[13px] min-w-0`}>
      <p className="text-sv-muted text-[12px]">{label}</p>
      <p className={`mt-0.5 text-[19px] font-extrabold ${tone ?? 'text-sv-ink'} truncate`}>{value}</p>
      {sub && <div className="mt-0.5 text-[11px] text-sv-muted">{sub}</div>}
    </div>
  );
}
