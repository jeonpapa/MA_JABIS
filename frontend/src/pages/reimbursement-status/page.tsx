import { useState } from 'react';
import PipelineBoard from './components/PipelineBoard';
import IntelligenceReports from './components/IntelligenceReports';
import AnnualSchedule from './components/AnnualSchedule';

export default function ReimbursementStatusPage() {
  const [isDark, setIsDark] = useState(false);

  const pageBg = isDark ? 'bg-[#0D1117]' : 'bg-gray-50';
  const headerBg = isDark ? 'border-[#1E2530]' : 'border-gray-200';
  const headerText = isDark ? 'text-white' : 'text-gray-900';
  const headerSub = isDark ? 'text-[#8B9BB4]' : 'text-gray-500';

  return (
    <div className={`min-h-screen ${pageBg} ${isDark ? 'text-white' : 'text-gray-900'}`}>
      {/* Header */}
      <div className={`px-8 pt-7 pb-5 border-b flex items-center justify-between ${headerBg}`}>
        <div>
          <div className="flex items-center gap-2.5 mb-1">
            <span className={`w-6 h-6 flex items-center justify-center ${isDark ? 'text-teal-400' : 'text-teal-600'}`}>
              <i className="ri-article-line text-lg"></i>
            </span>
            <h1 className={`text-2xl font-bold tracking-tight ${headerText}`}>Reimbursement Status</h1>
          </div>
          <p className={`text-sm ${headerSub}`}>
            약제 급여 등재 파이프라인 현황 및 심의위원회 회차별 인텔리전스 보고서
          </p>
        </div>

        {/* Theme Toggle */}
        <button
          onClick={() => setIsDark(!isDark)}
          className={`w-10 h-10 flex items-center justify-center rounded-lg cursor-pointer transition-all ${
            isDark
              ? 'bg-[#1E2530] text-[#F59E0B] hover:bg-[#2A3545]'
              : 'bg-gray-100 text-gray-500 hover:bg-gray-200'
          }`}
          title={isDark ? '라이트 모드' : '다크 모드'}
        >
          <i className={isDark ? 'ri-sun-line text-lg' : 'ri-moon-line text-lg'}></i>
        </button>
      </div>

      <div className="px-8 py-7 space-y-6">
        <AnnualSchedule isDark={isDark} />
        <PipelineBoard isDark={isDark} />
        <IntelligenceReports isDark={isDark} />
      </div>
    </div>
  );
}