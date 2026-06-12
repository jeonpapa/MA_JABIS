import { useState } from 'react';
import { useApi } from '@/hooks/useApi';
import { fetchMeetings, MeetingSchedule } from '@/api/reimbStatus';
import MeetingResultModal from './MeetingResultModal';

function MeetingItem({ m, isDark, onClick }: { m: MeetingSchedule; isDark: boolean; onClick?: () => void }) {
  const isUrgent = !m.isPast && m.daysUntil <= 14 && m.daysUntil > 0;
  const isNear = !m.isPast && m.daysUntil <= 30 && m.daysUntil > 0;
  const isClickable = m.isPast && !!onClick;

  const itemClasses = m.isPast
    ? `opacity-35 border-transparent${isClickable ? ' cursor-pointer hover:opacity-60 hover:border-dashed hover:border-gray-400/30' : ''}`
    : isUrgent
    ? `border-red-300/60 ${isDark ? 'bg-red-400/8' : 'bg-red-50'}`
    : isNear
    ? `border-amber-300/60 ${isDark ? 'bg-amber-400/8' : 'bg-amber-50'}`
    : `${isDark ? 'border-[#1E2530] bg-[#0D1117]/50' : 'border-gray-100 bg-white'}`;

  return (
    <div
      className={`flex items-center gap-1 px-2 py-1.5 rounded-lg border transition-all overflow-hidden ${itemClasses}`}
      onClick={isClickable ? onClick : undefined}
    >
      <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
        m.type === 'cancer'
          ? (isDark ? 'bg-teal-400' : 'bg-teal-500')
          : (isDark ? 'bg-violet-400' : 'bg-violet-500')
      }`} />
      <span className={`text-[11px] font-bold flex-shrink-0 ${isDark ? 'text-[#8B9BB4]' : 'text-gray-500'}`}>
        {m.cycle}
      </span>
      <span className={`text-[11px] font-semibold flex-shrink-0 ${isDark ? 'text-white' : 'text-gray-800'}`}>
        {m.date.slice(5)}
      </span>
      {/* 요일 — 공간 부족 시 줄어들며 잘림 (D-day 오버플로우 방지) */}
      <span className={`text-[10px] truncate min-w-0 ${isDark ? 'text-[#5A6A80]' : 'text-gray-400'}`}>
        {m.dayOfWeek}
      </span>
      {!m.isPast && (
        <span className={`text-[10px] font-bold flex-shrink-0 ml-auto pl-0.5 ${
          isUrgent ? 'text-red-500' : isNear ? 'text-amber-500' : isDark ? 'text-teal-400' : 'text-teal-600'
        }`}>
          D-{m.daysUntil}
        </span>
      )}
      {m.isPast && (
        <span className={`text-[10px] flex-shrink-0 ml-auto pl-0.5 ${isDark ? 'text-[#4A5568]' : 'text-gray-400'}`}>
          {isClickable ? '결과' : '완료'}
        </span>
      )}
    </div>
  );
}

export default function AnnualSchedule({ isDark }: { isDark: boolean }) {
  const [filter, setFilter] = useState<'all' | 'cancer' | 'evaluation'>('all');
  const [selectedMeeting, setSelectedMeeting] = useState<MeetingSchedule | null>(null);

  const { data, loading, error, reload } = useApi(fetchMeetings);
  // 2026년 연간 일정 — 결과 링크용으로 적재한 2025 세션은 캘린더에서 제외
  const meetingSchedules = (data ?? []).filter(m => m.year === 2026);

  const months = [
    '1월', '2월', '3월', '4월', '5월', '6월',
    '7월', '8월', '9월', '10월', '11월', '12월',
  ];

  const cancerCount = meetingSchedules.filter(s => s.type === 'cancer' && !s.isPast).length;
  const evalCount = meetingSchedules.filter(s => s.type === 'evaluation' && !s.isPast).length;
  const cancerTotal = meetingSchedules.filter(s => s.type === 'cancer').length;
  const evalTotal = meetingSchedules.filter(s => s.type === 'evaluation').length;
  const urgentCount = meetingSchedules.filter(s => !s.isPast && s.daysUntil <= 14 && s.daysUntil > 0).length;

  const today = new Date();
  const todayLabel = `${today.getFullYear()}.${String(today.getMonth() + 1).padStart(2, '0')}.${String(today.getDate()).padStart(2, '0')} (${['일', '월', '화', '수', '목', '금', '토'][today.getDay()]})`;
  const nextMeeting = meetingSchedules
    .filter(s => s.daysUntil > 0)
    .sort((a, b) => a.daysUntil - b.daysUntil)[0];

  const containerBg = isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-white border-gray-200';
  const filterBg = isDark ? 'bg-[#0D1117] border-[#1E2530]' : 'bg-gray-50 border-gray-200';
  const cardBg = isDark ? 'bg-[#0D1117] border-[#1E2530] hover:border-[#2A3545]' : 'bg-gray-50/80 border-gray-100 hover:border-gray-300';

  return (
    <>
      <div className={`rounded-2xl border p-6 ${containerBg}`}>
        <div className="flex items-center justify-between mb-5">
          <div className="flex items-center gap-2.5">
            <span className={`w-6 h-6 flex items-center justify-center ${isDark ? 'text-teal-400' : 'text-teal-600'}`}>
              <i className="ri-calendar-todo-line text-base"></i>
            </span>
            <div>
              <h2 className={`font-bold text-base ${isDark ? 'text-white' : 'text-gray-900'}`}>2026년 연간 일정</h2>
              <p className={`text-xs mt-0.5 ${isDark ? 'text-[#8B9BB4]' : 'text-gray-500'}`}>
                중증(암)질환심의위원회 · 약제급여평가위원회 회차 일정
              </p>
            </div>
          </div>

          <div className={`flex items-center gap-1 rounded-lg p-1 border ${filterBg}`}>
            <button
              onClick={() => setFilter('all')}
              className={`px-3.5 py-2 rounded-md text-[11px] font-semibold cursor-pointer whitespace-nowrap transition-all ${
                filter === 'all'
                  ? 'bg-teal-500 text-white'
                  : isDark ? 'text-[#8B9BB4] hover:text-white' : 'text-gray-500 hover:text-gray-900'
              }`}
            >
              전체
            </button>
            <button
              onClick={() => setFilter('cancer')}
              className={`px-3.5 py-2 rounded-md text-[11px] font-semibold cursor-pointer whitespace-nowrap transition-all flex items-center gap-1.5 ${
                filter === 'cancer'
                  ? 'bg-teal-500 text-white'
                  : isDark ? 'text-[#8B9BB4] hover:text-white' : 'text-gray-500 hover:text-gray-900'
              }`}
            >
              <span className="w-2 h-2 rounded-full bg-teal-500"></span>
              암질심 {cancerCount}
            </button>
            <button
              onClick={() => setFilter('evaluation')}
              className={`px-3.5 py-2 rounded-md text-[11px] font-semibold cursor-pointer whitespace-nowrap transition-all flex items-center gap-1.5 ${
                filter === 'evaluation'
                  ? 'bg-violet-500 text-white'
                  : isDark ? 'text-[#8B9BB4] hover:text-white' : 'text-gray-500 hover:text-gray-900'
              }`}
            >
              <span className="w-2 h-2 rounded-full bg-violet-500"></span>
              약평위 {evalCount}
            </button>
          </div>
        </div>

        {loading && (
          <div className={`flex items-center justify-center gap-2 py-14 text-sm ${isDark ? 'text-[#8B9BB4]' : 'text-gray-500'}`}>
            <i className="ri-loader-4-line animate-spin text-lg"></i>
            연간 일정 불러오는 중...
          </div>
        )}

        {!loading && error && (
          <div className={`text-center py-14 ${isDark ? 'text-[#8B9BB4]' : 'text-gray-500'}`}>
            <span className="w-12 h-12 flex items-center justify-center mx-auto mb-3 text-red-400">
              <i className="ri-error-warning-line text-3xl"></i>
            </span>
            <p className="text-sm mb-3">연간 일정을 불러오지 못했습니다: {error}</p>
            <button
              onClick={reload}
              className="px-4 py-2 rounded-lg text-xs font-semibold bg-teal-500 text-white hover:bg-teal-600 cursor-pointer transition-colors"
            >
              다시 시도
            </button>
          </div>
        )}

        {!loading && !error && meetingSchedules.length === 0 && (
          <div className={`text-center py-14 text-sm ${isDark ? 'text-[#5A6A80]' : 'text-gray-400'}`}>
            등록된 회차 일정이 없습니다
          </div>
        )}

        {!loading && !error && meetingSchedules.length > 0 && (
          <>
            <div className={`flex items-center gap-4 mb-5`}>
              <div className="flex items-center gap-1.5 text-[11px]">
                <span className="w-2.5 h-2.5 rounded-full bg-teal-500"></span>
                <span className={isDark ? 'text-[#8B9BB4]' : 'text-gray-500'}>암질심</span>
                <span className={`font-bold ${isDark ? 'text-white' : 'text-gray-900'}`}>{cancerTotal}회차</span>
                <span className={isDark ? 'text-[#5A6A80]' : 'text-gray-400'}>(수요일)</span>
              </div>
              <div className={`w-px h-4 ${isDark ? 'bg-[#2A3545]' : 'bg-gray-200'}`}></div>
              <div className="flex items-center gap-1.5 text-[11px]">
                <span className="w-2.5 h-2.5 rounded-full bg-violet-500"></span>
                <span className={isDark ? 'text-[#8B9BB4]' : 'text-gray-500'}>약평위</span>
                <span className={`font-bold ${isDark ? 'text-white' : 'text-gray-900'}`}>{evalTotal}회차</span>
                <span className={isDark ? 'text-[#5A6A80]' : 'text-gray-400'}>(목요일)</span>
              </div>
              {urgentCount > 0 && (
                <>
                  <div className={`w-px h-4 ${isDark ? 'bg-[#2A3545]' : 'bg-gray-200'}`}></div>
                  <div className="flex items-center gap-1.5 text-[11px]">
                    <span className="w-2.5 h-2.5 rounded-full bg-red-400"></span>
                    <span className="text-red-500 font-bold">{urgentCount}회차</span>
                    <span className={isDark ? 'text-[#8B9BB4]' : 'text-gray-500'}>2주 이내</span>
                  </div>
                </>
              )}
            </div>

            <div className="flex items-center gap-4 mb-4 text-[11px]">
              <div className="flex items-center gap-1.5">
                <span className={`w-2.5 h-2.5 rounded-full ${isDark ? 'bg-[#4A5568]' : 'bg-gray-300'}`}></span>
                <span className={isDark ? 'text-[#5A6A80]' : 'text-gray-400'}>지남 (클릭: 결과 보기)</span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="w-2.5 h-2.5 rounded-full bg-amber-400"></span>
                <span className={isDark ? 'text-[#8B9BB4]' : 'text-gray-500'}>30일 이내</span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="w-2.5 h-2.5 rounded-full bg-red-400"></span>
                <span className={isDark ? 'text-[#8B9BB4]' : 'text-gray-500'}>2주 이내</span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="w-2.5 h-2.5 rounded-full bg-teal-500"></span>
                <span className={isDark ? 'text-[#8B9BB4]' : 'text-gray-500'}>예정</span>
              </div>
            </div>

            <div className="grid grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-3">
              {months.map((monthLabel, idx) => {
                const month = idx + 1;
                const cancerMeetings = meetingSchedules.filter(s => s.month === month && s.type === 'cancer');
                const evalMeetings = meetingSchedules.filter(s => s.month === month && s.type === 'evaluation');
                const showCancer = filter === 'all' || filter === 'cancer';
                const showEval = filter === 'all' || filter === 'evaluation';
                const visible = [
                  ...(showCancer ? cancerMeetings : []),
                  ...(showEval ? evalMeetings : []),
                ];

                return (
                  <div key={month} className={`rounded-xl border p-3.5 transition-all ${cardBg}`}>
                    <div className="flex items-center justify-between mb-2.5">
                      <span className={`text-sm font-bold ${isDark ? 'text-white' : 'text-gray-900'}`}>{monthLabel}</span>
                      <span className={`text-[10px] ${isDark ? 'text-[#5A6A80]' : 'text-gray-400'}`}>2026</span>
                    </div>
                    <div className="space-y-1.5">
                      {visible.map(m => (
                        <MeetingItem
                          key={m.id}
                          m={m}
                          isDark={isDark}
                          onClick={m.isPast ? () => setSelectedMeeting(m) : undefined}
                        />
                      ))}
                      {visible.length === 0 && (
                        <p className={`text-[10px] text-center py-2 ${isDark ? 'text-[#4A5568]' : 'text-gray-300'}`}>-</p>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>

            <div className={`mt-5 pt-3.5 border-t flex items-center gap-2 ${
              isDark ? 'border-[#1E2530]' : 'border-gray-100'
            }`}>
              <span className={`w-4 h-4 flex items-center justify-center ${isDark ? 'text-[#5A6A80]' : 'text-gray-400'}`}>
                <i className="ri-time-line text-xs"></i>
              </span>
              <span className={`text-[11px] ${isDark ? 'text-[#5A6A80]' : 'text-gray-400'}`}>기준일: {todayLabel}</span>
              <span className={`text-[11px] ml-auto font-medium ${isDark ? 'text-[#8B9BB4]' : 'text-gray-500'}`}>
                {nextMeeting
                  ? `다음 회차: ${nextMeeting.month}월 ${Number(nextMeeting.date.slice(8, 10))}일 ${nextMeeting.typeLabel} ${nextMeeting.cycle} (D-${nextMeeting.daysUntil})`
                  : '예정된 회차 없음'}
              </span>
            </div>
          </>
        )}
      </div>

      {selectedMeeting && (
        <MeetingResultModal
          meeting={selectedMeeting}
          isDark={isDark}
          onClose={() => setSelectedMeeting(null)}
        />
      )}
    </>
  );
}
