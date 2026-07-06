import { useState } from 'react';
import { useLocation } from 'react-router-dom';
import { pageLabelFor } from './pageLabels';
import ServiceRequestModal, { type CapturedPageContext } from './ServiceRequestModal';

// 글로벌 "개선 요청" FAB — 인증된 Layout 블록에만 마운트되어 /login 에는 노출되지 않음.
// 캡처 컨텍스트: path/query/page_label/user_agent/captured_at 만.
// 토큰·쿠키·localStorage 인증값은 절대 포함하지 않는다.
export default function ServiceRequestButton() {
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const [captured, setCaptured] = useState<CapturedPageContext | null>(null);

  const openModal = () => {
    const pageLabel = pageLabelFor(location.pathname);
    setCaptured({
      page_path: location.pathname,
      page_label: pageLabel,
      source_url: window.location.href,
      context: {
        path: location.pathname,
        query: location.search || '',
        page_label: pageLabel,
        user_agent: navigator.userAgent,
        captured_at: new Date().toISOString(),
      },
    });
    setOpen(true);
  };

  return (
    <>
      <button
        onClick={openModal}
        title="서비스 보완/개선 요청"
        className="fixed bottom-6 right-6 z-50 flex items-center gap-2 bg-[#00E5CC] text-[#0A0E1A] text-sm font-bold px-4 py-3 rounded-full shadow-lg shadow-[#00E5CC]/25 hover:bg-[#00C9B1] transition-colors cursor-pointer whitespace-nowrap"
      >
        <i className="ri-customer-service-2-line text-base"></i>
        개선 요청
      </button>
      {open && captured && (
        <ServiceRequestModal captured={captured} onClose={() => setOpen(false)} />
      )}
    </>
  );
}
