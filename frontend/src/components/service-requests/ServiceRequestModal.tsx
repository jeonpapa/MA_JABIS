import { useState } from 'react';
import { Link } from 'react-router-dom';
import {
  submitServiceRequest,
  REQUEST_TYPE_LABELS,
  PRIORITY_LABELS,
  type SRRequestType,
  type SRPriority,
} from '@/api/serviceRequests';

export interface CapturedPageContext {
  page_path: string;
  page_label: string;
  source_url: string;
  context: Record<string, unknown>;
}

// SearchFeedbackModal(analog-search) 미러 — 다크 카드 버전.
export default function ServiceRequestModal({
  captured,
  onClose,
}: {
  captured: CapturedPageContext;
  onClose: () => void;
}) {
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [expectedOutcome, setExpectedOutcome] = useState('');
  const [requestType, setRequestType] = useState<SRRequestType>('improvement');
  const [priority, setPriority] = useState<SRPriority>('medium');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [sent, setSent] = useState(false);

  const submit = async () => {
    if (!title.trim()) {
      setErr('제목을 입력해 주세요.');
      return;
    }
    setBusy(true);
    setErr('');
    try {
      await submitServiceRequest({
        title: title.trim(),
        body: body.trim(),
        expected_outcome: expectedOutcome.trim(),
        request_type: requestType,
        priority,
        page_path: captured.page_path,
        page_label: captured.page_label,
        source_url: captured.source_url,
        context: captured.context,
      });
      setSent(true);
    } catch (e) {
      setErr('접수 실패: ' + (e instanceof Error ? e.message : String(e)));
    } finally {
      setBusy(false);
    }
  };

  const inputCls =
    'w-full mt-1 text-sm bg-[#0D1117] border border-[#1E2530] rounded-lg px-3 py-2 text-white placeholder-[#4A5568] focus:outline-none focus:border-[#00E5CC]/50 transition-colors';

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="bg-[#161B27] border border-[#1E2530] rounded-2xl max-w-md w-full max-h-[85vh] overflow-y-auto shadow-xl"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-[#1E2530]">
          <div className="flex items-center gap-2">
            <i className="ri-customer-service-2-line text-[#00E5CC] text-lg"></i>
            <h3 className="font-bold text-white">서비스 보완/개선 요청</h3>
          </div>
          <button onClick={onClose} className="text-[#4A5568] hover:text-white cursor-pointer">
            <i className="ri-close-line text-xl"></i>
          </button>
        </div>

        {sent ? (
          <div className="px-6 py-8 text-center space-y-3">
            <i className="ri-checkbox-circle-line text-emerald-400 text-4xl"></i>
            <p className="text-sm text-[#C9D4E3]">
              요청이 접수되었습니다. 처리 상태는{' '}
              <Link
                to="/my-requests"
                onClick={onClose}
                className="text-[#00E5CC] font-semibold hover:underline"
              >
                내 개선 요청
              </Link>
              에서 확인할 수 있습니다.
            </p>
            <button
              onClick={onClose}
              className="mt-1 bg-[#1E2530] text-[#8B9BB4] text-sm font-medium px-5 py-2 rounded-lg hover:bg-[#2A3441] cursor-pointer"
            >
              닫기
            </button>
          </div>
        ) : (
          <div className="px-6 py-5 space-y-4 text-sm">
            <p className="text-[#8B9BB4] leading-relaxed">
              대쉬보드에서 발견한 <span className="font-medium text-white">버그·개선점·신규 기능·데이터 요청</span>을
              남겨주세요. 관리자가 검토 후 개선 작업으로 연결합니다.
            </p>

            {/* 자동 캡처된 페이지 컨텍스트 (읽기 전용) */}
            <div className="rounded-lg bg-[#0D1117] border border-[#1E2530] px-3 py-2 text-xs text-[#8B9BB4] space-y-0.5">
              <p>
                <i className="ri-window-line mr-1"></i>페이지:{' '}
                <span className="font-medium text-white">{captured.page_label}</span>
              </p>
              <p className="truncate">
                <i className="ri-link mr-1"></i>경로:{' '}
                <span className="text-[#C9D4E3]">{captured.page_path}</span>
              </p>
            </div>

            <div>
              <label className="text-xs font-semibold text-[#8B9BB4]">
                제목 <span className="text-red-400">*</span>
              </label>
              <input
                value={title}
                onChange={e => setTitle(e.target.value)}
                autoFocus
                placeholder="예: 국내약가 테이블 정렬이 초기화됩니다"
                className={inputCls}
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-semibold text-[#8B9BB4]">유형</label>
                <select
                  value={requestType}
                  onChange={e => setRequestType(e.target.value as SRRequestType)}
                  className={`${inputCls} cursor-pointer`}
                >
                  {(Object.keys(REQUEST_TYPE_LABELS) as SRRequestType[]).map(t => (
                    <option key={t} value={t}>
                      {REQUEST_TYPE_LABELS[t]}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-xs font-semibold text-[#8B9BB4]">우선순위</label>
                <select
                  value={priority}
                  onChange={e => setPriority(e.target.value as SRPriority)}
                  className={`${inputCls} cursor-pointer`}
                >
                  {(Object.keys(PRIORITY_LABELS) as SRPriority[]).map(p => (
                    <option key={p} value={p}>
                      {PRIORITY_LABELS[p]}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div>
              <label className="text-xs font-semibold text-[#8B9BB4]">설명</label>
              <textarea
                value={body}
                onChange={e => setBody(e.target.value)}
                rows={3}
                placeholder="현상, 재현 방법, 문제라고 느낀 부분을 자유롭게 적어주세요."
                className={`${inputCls} resize-none`}
              />
            </div>

            <div>
              <label className="text-xs font-semibold text-[#8B9BB4]">기대 결과</label>
              <textarea
                value={expectedOutcome}
                onChange={e => setExpectedOutcome(e.target.value)}
                rows={2}
                placeholder="개선 후 어떤 동작/결과를 기대하는지 적어주세요."
                className={`${inputCls} resize-none`}
              />
            </div>

            {err && <p className="text-xs text-red-400">{err}</p>}

            <div className="flex justify-end gap-2 pt-1">
              <button
                onClick={onClose}
                className="text-[#8B9BB4] text-sm px-4 py-2 rounded-lg hover:bg-white/5 cursor-pointer"
              >
                취소
              </button>
              <button
                onClick={submit}
                disabled={busy}
                className="flex items-center gap-1.5 bg-[#00E5CC] text-[#0A0E1A] text-sm font-bold px-5 py-2 rounded-lg hover:bg-[#00C9B1] transition-colors disabled:opacity-60 cursor-pointer"
              >
                <i className={busy ? 'ri-loader-4-line animate-spin' : 'ri-send-plane-line'}></i>
                {busy ? '접수 중…' : '요청 보내기'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
