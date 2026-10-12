import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { REASON_MAX, REASON_MIN } from '@/api/stakeholder';
import type { ToastState } from './uiHelpers';

// 모니터링 인물 관리·모니터링 감사(admin) 공용 UI — 모달 셸, 사유 입력 모달(prompt 금지), 확인 모달, 토스트.
// 스타일은 기존 admin 페이지(service-requests / daily-mailing-kanban)의 다크 팔레트·입력 클래스를 따른다.

export const inputCls =
  'w-full bg-[#0D1117] border border-[#1E2530] rounded-lg px-3 py-2 text-white text-xs placeholder-[#4A5568] focus:outline-none focus:border-[#00E5CC]/50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed';

export const btnPrimary =
  'inline-flex items-center justify-center gap-1.5 rounded-lg bg-[#00E5CC] px-3.5 py-2 text-xs font-bold text-[#0A0E1A] hover:bg-[#00E5CC]/90 cursor-pointer disabled:cursor-not-allowed disabled:bg-[#1E2530] disabled:text-[#4A5568] transition-colors';
export const btnGhost =
  'inline-flex items-center justify-center gap-1.5 rounded-lg border border-[#1E2530] bg-[#161B27] px-3.5 py-2 text-xs font-semibold text-[#8B9BB4] hover:text-white hover:border-[#2A3545] cursor-pointer disabled:cursor-not-allowed disabled:opacity-50 transition-colors';
export const btnDanger =
  'inline-flex items-center justify-center gap-1.5 rounded-lg border border-red-500/30 bg-red-500/10 px-3.5 py-2 text-xs font-semibold text-red-400 hover:bg-red-500/20 cursor-pointer disabled:cursor-not-allowed disabled:opacity-50 transition-colors';

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
      className="fixed inset-0 z-[60] bg-black/60 flex items-center justify-center p-4"
      onClick={() => !busy && onClose()}
    >
      <div
        ref={boxRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={`bg-[#161B27] rounded-2xl border border-[#1E2530] w-full ${wide ? 'max-w-4xl' : 'max-w-lg'} max-h-[90vh] flex flex-col`}
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-3 px-5 pt-4 pb-3 border-b border-[#1E2530]">
          <h2 id={titleId} className="text-white text-sm font-bold truncate">
            {title}
          </h2>
          <button
            onClick={onClose}
            disabled={busy}
            aria-label="닫기"
            className="text-[#8B9BB4] hover:text-white cursor-pointer disabled:opacity-40"
          >
            <i className="ri-close-line text-lg"></i>
          </button>
        </div>
        <div className="px-5 py-4 overflow-y-auto">{children}</div>
        {footer && <div className="px-5 pb-4 pt-1 flex flex-wrap justify-end gap-2">{footer}</div>}
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
      {description && <div className="mb-3 text-xs text-[#8B9BB4] leading-relaxed">{description}</div>}
      <label htmlFor={fieldId} className="block text-[#8B9BB4] text-[11px] font-semibold mb-1">
        사유 <span className="text-[#4A5568] font-normal">({REASON_MIN}~{REASON_MAX}자, 처리 이력에 기록됩니다)</span>
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
      <div className="mt-1 flex justify-between text-[10px]">
        <span className={tooShort && len > 0 ? 'text-[#F59E0B]' : 'text-[#4A5568]'}>
          {tooShort ? `사유를 ${REASON_MIN}자 이상 입력해야 진행할 수 있습니다.` : tooLong ? `${REASON_MAX}자를 넘었습니다.` : ' '}
        </span>
        <span className={tooLong ? 'text-red-400' : 'text-[#4A5568]'}>
          {len}/{REASON_MAX}
        </span>
      </div>
      {err && (
        <p role="alert" className="mt-3 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300 whitespace-pre-wrap">
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
      <div className="text-xs text-[#C9D1DD] leading-relaxed">{body}</div>
      {err && (
        <p role="alert" className="mt-3 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
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
      className={`fixed bottom-6 left-1/2 -translate-x-1/2 z-[70] max-w-[90vw] rounded-xl px-4 py-3 text-xs font-semibold shadow-lg border ${
        toast.tone === 'error'
          ? 'bg-[#2A1418] border-red-500/40 text-red-300'
          : 'bg-[#0F2A27] border-[#00E5CC]/40 text-[#00E5CC]'
      }`}
    >
      {toast.msg}
    </div>
  );
}

export function StatCard({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: string }) {
  return (
    <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-4 min-w-0">
      <p className="text-[#8B9BB4] text-[11px] font-semibold">{label}</p>
      <p className={`mt-1 text-2xl font-bold ${tone ?? 'text-white'} truncate`}>{value}</p>
      {sub && <div className="mt-1 text-[11px] text-[#4A5568]">{sub}</div>}
    </div>
  );
}
