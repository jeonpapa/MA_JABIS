import { useEffect, useState } from 'react';
import {
  listMailSubscriptions, createMailSubscription, updateMailSubscription,
  deleteMailSubscription, testSendMailSubscription,
  type MailSubscription,
} from '@/api/mailSubscriptions';

const PRESET_KEYWORDS = [
  '약가 인하', '급여 등재', '보험 적용', '심평원', '건강보험공단', '보건복지부',
  '임상시험', '허가 승인', '파이프라인', '바이오시밀러', '제네릭', 'RSA',
  '위험분담제', '선별급여', '비급여', '항암제', '면역항암제', '표적치료제',
  'HTA', '약제급여평가위원회', '약가협상', '실거래가', '사용량-약가 연동',
];

const MEDIA_CATEGORIES = [
  {
    category: '전문지',
    items: [
      { id: 'medi', label: '메디칼타임즈' },
      { id: 'doctorsnews', label: '청년의사' },
      { id: 'medigate', label: '메디게이트뉴스' },
      { id: 'yakup', label: '약업신문' },
      { id: 'kpanews', label: '한국제약바이오협회' },
      { id: 'hitnews', label: 'HIT뉴스' },
    ],
  },
  {
    category: '일간지',
    items: [
      { id: 'chosun', label: '조선일보' },
      { id: 'joongang', label: '중앙일보' },
      { id: 'donga', label: '동아일보' },
      { id: 'hani', label: '한겨레' },
      { id: 'kyunghyang', label: '경향신문' },
    ],
  },
  {
    category: '경제전문지',
    items: [
      { id: 'hankyung', label: '한국경제' },
      { id: 'maeil', label: '매일경제' },
      { id: 'edaily', label: '이데일리' },
      { id: 'mt', label: '머니투데이' },
      { id: 'fnews', label: '파이낸셜뉴스' },
    ],
  },
  {
    category: '방송/온라인',
    items: [
      { id: 'ytn', label: 'YTN' },
      { id: 'kbs', label: 'KBS' },
      { id: 'mbc', label: 'MBC' },
      { id: 'naver', label: '네이버 뉴스' },
      { id: 'daum', label: '다음 뉴스' },
    ],
  },
];

export default function DailyMailingPage() {
  const [isDark, setIsDark] = useState(false);
  const [selectedKeywords, setSelectedKeywords] = useState<string[]>(['약가 인하', '급여 등재', '심평원']);
  const [customKeyword, setCustomKeyword] = useState('');
  const [selectedMedia, setSelectedMedia] = useState<string[]>(['medi', 'yakup', 'hankyung']);
  const [schedule, setSchedule] = useState<'Daily' | 'Weekly'>('Daily');
  const [scheduleTime, setScheduleTime] = useState('08:00');
  const [weekDay, setWeekDay] = useState('Monday');
  const [emailInput, setEmailInput] = useState('');
  const [emailList, setEmailList] = useState<string[]>(['marketaccess@msd.com']);
  const [settingName, setSettingName] = useState('');
  const [savedSettings, setSavedSettings] = useState<MailSubscription[]>([]);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [submitStatus, setSubmitStatus] = useState<'idle' | 'success' | 'error'>('idle');
  const [submitMessage, setSubmitMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [testingId, setTestingId] = useState<number | null>(null);
  const [activeTab, setActiveTab] = useState<'new' | 'saved'>('new');

  const reload = async () => {
    setListError(null);
    try {
      const r = await listMailSubscriptions();
      setSavedSettings(r.items);
    } catch (e) {
      setListError(e instanceof Error ? e.message : '설정 목록 로드 실패');
    } finally {
      setListLoading(false);
    }
  };

  useEffect(() => {
    reload();
  }, []);

  const toggleKeyword = (kw: string) => { setSelectedKeywords(prev => prev.includes(kw) ? prev.filter(k => k !== kw) : [...prev, kw]); };
  const addCustomKeyword = () => {
    const trimmed = customKeyword.trim();
    if (trimmed && !selectedKeywords.includes(trimmed)) { setSelectedKeywords(prev => [...prev, trimmed]); setCustomKeyword(''); }
  };
  const removeKeyword = (kw: string) => { setSelectedKeywords(prev => prev.filter(k => k !== kw)); };
  const toggleMedia = (id: string) => { setSelectedMedia(prev => prev.includes(id) ? prev.filter(m => m !== id) : [...prev, id]); };
  const toggleCategoryMedia = (items: { id: string }[]) => {
    const ids = items.map(i => i.id);
    const allSelected = ids.every(id => selectedMedia.includes(id));
    if (allSelected) { setSelectedMedia(prev => prev.filter(m => !ids.includes(m))); }
    else { setSelectedMedia(prev => [...new Set([...prev, ...ids])]); }
  };
  const addEmail = () => {
    const trimmed = emailInput.trim();
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (trimmed && emailRegex.test(trimmed) && !emailList.includes(trimmed)) { setEmailList(prev => [...prev, trimmed]); setEmailInput(''); }
  };
  const removeEmail = (email: string) => { setEmailList(prev => prev.filter(e => e !== email)); };
  const toggleSetting = async (id: number, next: boolean) => {
    try {
      await updateMailSubscription(id, { active: next });
      await reload();
    } catch (e) {
      alert(e instanceof Error ? e.message : '상태 변경 실패');
    }
  };

  const deleteSetting = async (id: number) => {
    if (!confirm('이 설정을 삭제하시겠습니까?')) return;
    try {
      await deleteMailSubscription(id);
      await reload();
    } catch (e) {
      alert(e instanceof Error ? e.message : '삭제 실패');
    }
  };

  const handleTestSend = async (id: number) => {
    setTestingId(id);
    try {
      const r = await testSendMailSubscription(id);
      if (r.ok && r.mode === 'smtp') {
        alert(`발송 완료 → ${r.recipients.join(', ')}`);
      } else if (r.ok && r.mode === 'dry-run') {
        alert(`[Dry-run] SMTP 미설정. ${r.message ?? ''}`);
      } else {
        alert(`발송 실패: ${r.message ?? ''}`);
      }
      await reload();
    } catch (e) {
      alert(e instanceof Error ? e.message : '발송 실패');
    } finally {
      setTestingId(null);
    }
  };

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (selectedKeywords.length === 0 || selectedMedia.length === 0 || emailList.length === 0) return;
    setSubmitting(true);
    try {
      await createMailSubscription({
        name: settingName.trim() || '새 메일링 설정',
        keywords: selectedKeywords,
        media: selectedMedia,
        schedule,
        time: scheduleTime,
        weekDay: schedule === 'Weekly' ? weekDay : null,
        emails: emailList,
        active: true,
      });
      setSubmitStatus('success');
      setSubmitMessage('메일링 설정이 저장되었습니다. 설정된 스케줄에 따라 발송됩니다.');
      setSettingName('');
      await reload();
      setActiveTab('saved');
      setTimeout(() => { setSubmitStatus('idle'); setSubmitMessage(null); }, 3000);
    } catch (err) {
      setSubmitStatus('error');
      setSubmitMessage(err instanceof Error ? err.message : '저장 중 오류가 발생했습니다. 다시 시도해주세요.');
      setTimeout(() => { setSubmitStatus('idle'); setSubmitMessage(null); }, 4000);
    } finally {
      setSubmitting(false);
    }
  };

  const pageBg = isDark ? 'bg-[#0D1117]' : 'bg-gray-50';
  const headerBorder = isDark ? 'border-[#1E2530]' : 'border-gray-200';
  const cardBg = isDark ? 'bg-[#161B27]' : 'bg-white';
  const cardBorder = isDark ? 'border-[#1E2530]' : 'border-gray-200';
  const textMain = isDark ? 'text-white' : 'text-gray-900';
  const textSub = isDark ? 'text-[#8B9BB4]' : 'text-gray-500';
  const textMuted = isDark ? 'text-[#4A5568]' : 'text-gray-400';
  const accentColor = isDark ? 'text-[#00E5CC]' : 'text-teal-600';
  const accentBg = isDark ? 'bg-[#00E5CC]/10' : 'bg-teal-50';
  const accentBorder = isDark ? 'border-[#00E5CC]/30' : 'border-teal-300';
  const inputBg = isDark ? 'bg-[#0D1117] border-[#1E2530]' : 'bg-gray-50 border-gray-200';
  const inputFocus = isDark ? 'focus:border-[#00E5CC]/50' : 'focus:border-teal-300';
  const inputText = isDark ? 'text-white placeholder-[#4A5568]' : 'text-gray-900 placeholder-gray-400';
  const tabBg = isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-gray-100 border-gray-200';
  const tabActive = isDark ? 'bg-[#00E5CC] text-[#0A0E1A]' : 'bg-teal-600 text-white';
  const tabInactive = isDark ? 'text-[#8B9BB4] hover:text-white' : 'text-gray-500 hover:text-gray-900';
  const tagSelected = isDark ? 'bg-[#00E5CC]/15 border-[#00E5CC]/40 text-[#00E5CC]' : 'bg-teal-50 border-teal-300 text-teal-700';
  const tagDefault = isDark ? 'bg-[#0D1117] border-[#1E2530] text-[#8B9BB4] hover:text-white hover:border-[#2A3545]' : 'bg-gray-50 border-gray-200 text-gray-500 hover:text-gray-900 hover:border-gray-300';
  const mediaCardBg = isDark ? 'bg-[#0D1117] border-[#1E2530]' : 'bg-gray-50 border-gray-200';
  const mediaCheckSelected = isDark ? 'bg-[#00E5CC] border-[#00E5CC]' : 'bg-teal-600 border-teal-600';
  const mediaCheckDefault = isDark ? 'border-[#2A3545] group-hover:border-[#00E5CC]/50' : 'border-gray-300 group-hover:border-teal-400';
  const emailTagBg = isDark ? 'bg-[#0D1117] border-[#1E2530]' : 'bg-gray-50 border-gray-200';
  const savedCardBg = isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-white border-gray-200';
  const scheduleDaily = isDark ? 'bg-[#00E5CC]/10 text-[#00E5CC]' : 'bg-teal-50 text-teal-700';
  const scheduleWeekly = isDark ? 'bg-[#F59E0B]/10 text-[#F59E0B]' : 'bg-amber-50 text-amber-700';
  const sumBg = isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-white border-gray-200';
  const divider = isDark ? 'bg-[#0D1117] border-[#1E2530]' : 'bg-gray-50 border-gray-200';
  const previewBg = isDark ? 'bg-[#0D1117] border-[#1E2530]' : 'bg-gray-50 border-gray-200';

  return (
    <div className={`min-h-screen ${pageBg} ${isDark ? 'text-white' : 'text-gray-900'}`}>
      {/* Header */}
      <div className={`px-8 pt-8 pb-6 border-b ${headerBorder}`}>
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className={`w-5 h-5 flex items-center justify-center ${accentColor}`}><i className="ri-mail-settings-line"></i></span>
              <h1 className={`text-2xl font-bold ${textMain}`}>Daily Mailing Setting</h1>
            </div>
            <p className={`${textSub} text-sm`}>키워드 모니터링 및 자동 메일 발송 스케줄 설정</p>
          </div>
          <div className="flex items-center gap-2">
            <div className={`flex items-center gap-1 rounded-lg p-1 ${tabBg}`}>
              {[
                { key: 'new', label: '새 설정', icon: 'ri-add-circle-line' },
                { key: 'saved', label: '저장된 설정', icon: 'ri-list-settings-line' },
              ].map(tab => (
                <button key={tab.key} onClick={() => setActiveTab(tab.key as 'new' | 'saved')}
                  className={`flex items-center gap-1.5 px-4 py-1.5 rounded-md text-xs font-medium cursor-pointer whitespace-nowrap transition-all ${activeTab === tab.key ? tabActive : tabInactive}`}>
                  <span className="w-3.5 h-3.5 flex items-center justify-center"><i className={`${tab.icon} text-xs`}></i></span>
                  {tab.label}
                  {tab.key === 'saved' && <span className={`${isDark ? 'bg-[#00E5CC]/20 text-[#00E5CC]' : 'bg-teal-100 text-teal-700'} text-xs px-1.5 py-0.5 rounded-full ml-1`}>{savedSettings.length}</span>}
                </button>
              ))}
            </div>
            <button
              onClick={() => setIsDark(!isDark)}
              className={`w-9 h-9 flex items-center justify-center rounded-lg cursor-pointer transition-all ${isDark ? 'bg-[#1E2530] text-amber-400 hover:bg-[#2A3545]' : 'bg-gray-100 text-gray-500 hover:bg-gray-200'}`}
              title={isDark ? '라이트 모드' : '다크 모드'}>
              <i className={isDark ? 'ri-sun-line text-lg' : 'ri-moon-line text-lg'}></i>
            </button>
          </div>
        </div>
      </div>

      <div className="px-8 py-6">
        {submitStatus === 'success' && (
          <div className="mb-5 flex items-center gap-3 bg-teal-50 border border-teal-200 rounded-xl px-5 py-3">
            <span className="w-5 h-5 flex items-center justify-center text-teal-600"><i className="ri-checkbox-circle-line text-lg"></i></span>
            <p className="text-teal-700 text-sm font-medium">{submitMessage ?? '메일링 설정이 저장되었습니다. 설정된 스케줄에 따라 발송됩니다.'}</p>
          </div>
        )}
        {submitStatus === 'error' && (
          <div className="mb-5 flex items-center gap-3 bg-red-50 border border-red-200 rounded-xl px-5 py-3">
            <span className="w-5 h-5 flex items-center justify-center text-red-500"><i className="ri-error-warning-line text-lg"></i></span>
            <p className="text-red-600 text-sm font-medium">{submitMessage ?? '저장 중 오류가 발생했습니다. 다시 시도해주세요.'}</p>
          </div>
        )}

        {activeTab === 'new' && (
          <form data-readdy-form id="daily-mailing-form" onSubmit={handleSubmit} className="space-y-5">
            {/* Setting Name */}
            <div className={`${cardBg} rounded-2xl border ${cardBorder} p-6`}>
              <h3 className={`font-bold text-sm mb-4 flex items-center gap-2 ${textMain}`}>
                <span className={`w-5 h-5 flex items-center justify-center ${accentColor}`}><i className="ri-bookmark-line text-sm"></i></span>
                설정 이름
              </h3>
              <input type="text" name="settingName" placeholder="예: 약가 정책 모니터링, 경쟁사 동향 추적..." value={settingName} onChange={e => setSettingName(e.target.value)}
                className={`w-full rounded-xl px-4 py-3 text-sm focus:outline-none transition-colors ${inputBg} ${inputFocus} ${inputText}`} />
            </div>

            {/* Keywords */}
            <div className={`${cardBg} rounded-2xl border ${cardBorder} p-6`}>
              <h3 className={`font-bold text-sm mb-1 flex items-center gap-2 ${textMain}`}>
                <span className={`w-5 h-5 flex items-center justify-center ${accentColor}`}><i className="ri-price-tag-3-line text-sm"></i></span>
                모니터링 키워드
              </h3>
              <p className={`${textSub} text-xs mb-4`}>프리셋에서 선택하거나 직접 입력하세요</p>
              {selectedKeywords.length > 0 && (
                <div className={`flex flex-wrap gap-2 mb-4 p-3 rounded-xl border ${divider}`}>
                  {selectedKeywords.map(kw => (
                    <span key={kw} className={`flex items-center gap-1.5 border text-xs px-3 py-1.5 rounded-full ${tagSelected}`}>
                      {kw}
                      <button type="button" onClick={() => removeKeyword(kw)} className="w-3.5 h-3.5 flex items-center justify-center hover:opacity-70 cursor-pointer transition-colors"><i className="ri-close-line text-xs"></i></button>
                    </span>
                  ))}
                </div>
              )}
              <div className="flex flex-wrap gap-2 mb-4">
                {PRESET_KEYWORDS.map(kw => (
                  <button type="button" key={kw} onClick={() => toggleKeyword(kw)}
                    className={`px-3 py-1.5 rounded-full text-xs font-medium cursor-pointer whitespace-nowrap transition-all ${selectedKeywords.includes(kw) ? tagSelected : tagDefault}`}>
                    {selectedKeywords.includes(kw) && <i className="ri-check-line mr-1 text-xs"></i>}{kw}
                  </button>
                ))}
              </div>
              <div className="flex gap-2">
                <input type="text" placeholder="직접 키워드 입력..." value={customKeyword} onChange={e => setCustomKeyword(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && (e.preventDefault(), addCustomKeyword())}
                  className={`flex-1 rounded-xl px-4 py-2.5 text-sm focus:outline-none transition-colors ${inputBg} ${inputFocus} ${inputText}`} />
                <button type="button" onClick={addCustomKeyword}
                  className={`flex items-center gap-2 text-sm font-medium px-4 py-2.5 rounded-xl cursor-pointer whitespace-nowrap transition-colors border ${accentBg} ${accentBorder} ${accentColor} hover:opacity-80`}>
                  <span className="w-4 h-4 flex items-center justify-center"><i className="ri-add-line text-sm"></i></span>추가
                </button>
              </div>
            </div>

            {/* Media Selection */}
            <div className={`${cardBg} rounded-2xl border ${cardBorder} p-6`}>
              <h3 className={`font-bold text-sm mb-1 flex items-center gap-2 ${textMain}`}>
                <span className={`w-5 h-5 flex items-center justify-center ${accentColor}`}><i className="ri-newspaper-line text-sm"></i></span>
                모니터링 미디어
              </h3>
              <p className={`${textSub} text-xs mb-4`}>모니터링할 미디어를 선택하세요</p>
              <div className="grid grid-cols-2 gap-4">
                {MEDIA_CATEGORIES.map(cat => {
                  const allSelected = cat.items.every(i => selectedMedia.includes(i.id));
                  const someSelected = cat.items.some(i => selectedMedia.includes(i.id));
                  return (
                    <div key={cat.category} className={`rounded-xl border p-4 ${mediaCardBg}`}>
                      <div className="flex items-center justify-between mb-3">
                        <span className={`text-xs font-bold ${textMain}`}>{cat.category}</span>
                        <button type="button" onClick={() => toggleCategoryMedia(cat.items)}
                          className={`text-xs px-2.5 py-1 rounded-full cursor-pointer whitespace-nowrap transition-all ${allSelected ? tagSelected : someSelected ? (isDark ? 'bg-[#F59E0B]/15 text-[#F59E0B] border border-[#F59E0B]/30' : 'bg-amber-50 text-amber-700 border border-amber-300') : (isDark ? 'bg-[#161B27] text-[#8B9BB4] border border-[#1E2530] hover:text-white' : 'bg-white text-gray-500 border border-gray-200 hover:text-gray-900')}`}>
                          {allSelected ? '전체 해제' : '전체 선택'}
                        </button>
                      </div>
                      <div className="space-y-2">
                        {cat.items.map(item => (
                          <label key={item.id} className="flex items-center gap-2.5 cursor-pointer group">
                            <div onClick={() => toggleMedia(item.id)}
                              className={`w-4 h-4 rounded flex items-center justify-center flex-shrink-0 border transition-all cursor-pointer ${selectedMedia.includes(item.id) ? mediaCheckSelected : mediaCheckDefault}`}>
                              {selectedMedia.includes(item.id) && <i className="ri-check-line text-white text-xs"></i>}
                            </div>
                            <span onClick={() => toggleMedia(item.id)} className={`text-xs transition-colors ${selectedMedia.includes(item.id) ? textMain : textSub} group-hover:${textMain}`}>{item.label}</span>
                          </label>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
              <div className="mt-3 flex items-center gap-2">
                <span className={`${textSub} text-xs`}>선택된 미디어:</span>
                <span className={`text-xs font-bold ${accentColor}`}>{selectedMedia.length}개</span>
              </div>
            </div>

            {/* Schedule */}
            <div className={`${cardBg} rounded-2xl border ${cardBorder} p-6`}>
              <h3 className={`font-bold text-sm mb-1 flex items-center gap-2 ${textMain}`}>
                <span className={`w-5 h-5 flex items-center justify-center ${accentColor}`}><i className="ri-calendar-schedule-line text-sm"></i></span>
                발송 스케줄
              </h3>
              <p className={`${textSub} text-xs mb-4`}>메일 발송 주기와 시간을 설정하세요</p>
              <div className="flex items-start gap-6 flex-wrap">
                <div className={`flex items-center gap-1 rounded-xl p-1 ${tabBg}`}>
                  {(['Daily', 'Weekly'] as const).map(s => (
                    <button type="button" key={s} onClick={() => setSchedule(s)}
                      className={`px-5 py-2 rounded-lg text-sm font-semibold cursor-pointer whitespace-nowrap transition-all ${schedule === s ? tabActive : tabInactive}`}>{s}</button>
                  ))}
                </div>
                {schedule === 'Weekly' && (
                  <div className="flex items-center gap-2 flex-wrap">
                    {['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'].map(day => (
                      <button type="button" key={day} onClick={() => setWeekDay(day)}
                        className={`px-3 py-2 rounded-lg text-xs font-medium cursor-pointer whitespace-nowrap transition-all ${weekDay === day ? tagSelected : tagDefault}`}>{day.slice(0, 3)}</button>
                    ))}
                  </div>
                )}
                <div className="flex items-center gap-3">
                  <span className={`w-4 h-4 flex items-center justify-center ${textSub}`}><i className="ri-time-line text-sm"></i></span>
                  <select name="scheduleTime" value={scheduleTime} onChange={e => setScheduleTime(e.target.value)}
                    className={`rounded-xl px-4 py-2 text-sm focus:outline-none cursor-pointer transition-colors ${inputBg} ${inputText}`}>
                    {['06:00','07:00','07:30','08:00','08:30','09:00','09:30','10:00','12:00','18:00','21:00'].map(t => (
                      <option key={t} value={t}>{t}</option>
                    ))}
                  </select>
                </div>
              </div>
              <div className={`mt-4 flex items-center gap-2 rounded-xl px-4 py-3 border ${previewBg}`}>
                <span className={`w-4 h-4 flex items-center justify-center ${accentColor}`}><i className="ri-information-line text-sm"></i></span>
                <p className={`${textSub} text-xs`}>{schedule === 'Daily' ? `매일 ${scheduleTime}에 메일이 발송됩니다` : `매주 ${weekDay} ${scheduleTime}에 메일이 발송됩니다`}</p>
              </div>
            </div>

            {/* Email */}
            <div className={`${cardBg} rounded-2xl border ${cardBorder} p-6`}>
              <h3 className={`font-bold text-sm mb-1 flex items-center gap-2 ${textMain}`}>
                <span className={`w-5 h-5 flex items-center justify-center ${accentColor}`}><i className="ri-mail-line text-sm"></i></span>
                수신 이메일
              </h3>
              <p className={`${textSub} text-xs mb-4`}>메일을 수신할 이메일 주소를 입력하세요</p>
              {emailList.length > 0 && (
                <div className="flex flex-wrap gap-2 mb-4">
                  {emailList.map(email => (
                    <span key={email} className={`flex items-center gap-2 text-xs px-3 py-2 rounded-xl border ${emailTagBg} ${textMain}`}>
                      <span className={`w-3.5 h-3.5 flex items-center justify-center ${accentColor}`}><i className="ri-mail-line text-xs"></i></span>
                      {email}
                      <button type="button" onClick={() => removeEmail(email)} className={`w-3.5 h-3.5 flex items-center justify-center hover:text-red-500 cursor-pointer transition-colors ${textMuted}`}><i className="ri-close-line text-xs"></i></button>
                    </span>
                  ))}
                </div>
              )}
              <div className="flex gap-2">
                <input type="email" name="email" placeholder="이메일 주소 입력..." value={emailInput} onChange={e => setEmailInput(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && (e.preventDefault(), addEmail())}
                  className={`flex-1 rounded-xl px-4 py-2.5 text-sm focus:outline-none transition-colors ${inputBg} ${inputFocus} ${inputText}`} />
                <button type="button" onClick={addEmail}
                  className={`flex items-center gap-2 text-sm font-medium px-4 py-2.5 rounded-xl cursor-pointer whitespace-nowrap transition-colors border ${accentBg} ${accentBorder} ${accentColor} hover:opacity-80`}>
                  <span className="w-4 h-4 flex items-center justify-center"><i className="ri-add-line text-sm"></i></span>추가
                </button>
              </div>
            </div>

            {/* Submit */}
            <div className={`flex items-center justify-between rounded-2xl border p-5 ${sumBg}`}>
              <div className={`flex items-center gap-4 text-xs ${textSub}`}>
                <span className="flex items-center gap-1.5"><span className={`w-3.5 h-3.5 flex items-center justify-center ${accentColor}`}><i className="ri-price-tag-3-line text-xs"></i></span>키워드 {selectedKeywords.length}개</span>
                <span className="flex items-center gap-1.5"><span className={`w-3.5 h-3.5 flex items-center justify-center ${accentColor}`}><i className="ri-newspaper-line text-xs"></i></span>미디어 {selectedMedia.length}개</span>
                <span className="flex items-center gap-1.5"><span className={`w-3.5 h-3.5 flex items-center justify-center ${accentColor}`}><i className="ri-mail-line text-xs"></i></span>수신자 {emailList.length}명</span>
                <span className="flex items-center gap-1.5"><span className={`w-3.5 h-3.5 flex items-center justify-center ${accentColor}`}><i className="ri-time-line text-xs"></i></span>{schedule} {scheduleTime}</span>
              </div>
              <button type="submit" disabled={submitting || selectedKeywords.length === 0 || selectedMedia.length === 0 || emailList.length === 0}
                className="flex items-center gap-2 bg-teal-600 text-white text-sm font-bold px-6 py-2.5 rounded-xl cursor-pointer whitespace-nowrap hover:bg-teal-700 transition-colors disabled:opacity-40 disabled:cursor-not-allowed">
                <span className="w-4 h-4 flex items-center justify-center"><i className={submitting ? 'ri-loader-4-line animate-spin text-sm' : 'ri-save-line text-sm'}></i></span>{submitting ? '저장 중…' : '설정 저장'}
              </button>
            </div>
          </form>
        )}

        {activeTab === 'saved' && (
          <div className="space-y-4">
            {listLoading && (
              <div className={`text-center py-16 text-sm ${textSub}`}>
                <i className="ri-loader-4-line animate-spin mr-2"></i>설정 로드 중…
              </div>
            )}
            {!listLoading && listError && (
              <div className={`text-center py-16 ${textMuted}`}>
                <span className="w-12 h-12 flex items-center justify-center mx-auto mb-3"><i className="ri-error-warning-line text-4xl text-red-400"></i></span>
                <p className="text-sm text-red-400">{listError}</p>
                <button onClick={() => { setListLoading(true); reload(); }} className={`mt-4 text-sm cursor-pointer hover:underline ${accentColor}`}>다시 시도</button>
              </div>
            )}
            {!listLoading && !listError && savedSettings.length === 0 && (
              <div className={`text-center py-16 ${textMuted}`}>
                <span className="w-12 h-12 flex items-center justify-center mx-auto mb-3"><i className="ri-mail-settings-line text-4xl"></i></span>
                <p className="text-sm">저장된 설정이 없습니다</p>
                <button onClick={() => setActiveTab('new')} className={`mt-4 text-sm cursor-pointer hover:underline ${accentColor}`}>새 설정 만들기</button>
              </div>
            )}
            {savedSettings.map(setting => (
              <div key={setting.id} className={`rounded-2xl border p-5 transition-all ${savedCardBg} ${setting.active ? '' : 'opacity-60'}`}>
                <div className="flex items-start justify-between mb-4">
                  <div className="flex items-center gap-3">
                    <div className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${setting.active ? 'bg-teal-500' : (isDark ? 'bg-[#4A5568]' : 'bg-gray-400')}`}></div>
                    <div>
                      <h4 className={`font-bold text-sm ${textMain}`}>{setting.name}</h4>
                      <p className={`${textSub} text-xs mt-0.5`}>
                        {setting.emails.slice(0, 2).join(', ')}
                        {setting.emails.length > 2 ? ` +${setting.emails.length - 2}명` : ''}
                      </p>
                      {setting.last_sent_at && (
                        <p className={`${textMuted} text-[10px] mt-0.5`}>마지막 발송 {new Date(setting.last_sent_at).toLocaleString('ko-KR')}</p>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className={`text-xs px-2.5 py-1 rounded-full font-medium ${setting.schedule === 'Daily' ? scheduleDaily : scheduleWeekly}`}>
                      {setting.schedule}
                      {setting.schedule === 'Weekly' && setting.weekDay ? ` ${setting.weekDay.slice(0, 3)}` : ''} {setting.time}
                    </span>
                    <button onClick={() => handleTestSend(setting.id)} disabled={testingId === setting.id}
                      className={`text-xs px-3 py-1 rounded-full cursor-pointer whitespace-nowrap transition-all border disabled:opacity-50 ${isDark ? 'border-[#00E5CC]/30 text-[#00E5CC] hover:bg-[#00E5CC]/10' : 'border-teal-300 text-teal-600 hover:bg-teal-50'}`}>
                      {testingId === setting.id ? '발송 중…' : '테스트 발송'}
                    </button>
                    <button onClick={() => toggleSetting(setting.id, !setting.active)}
                      className={`text-xs px-3 py-1 rounded-full cursor-pointer whitespace-nowrap transition-all border ${setting.active ? 'border-red-300 text-red-500 hover:bg-red-50' : 'border-teal-300 text-teal-600 hover:bg-teal-50'}`}>
                      {setting.active ? '비활성화' : '활성화'}
                    </button>
                    <button onClick={() => deleteSetting(setting.id)}
                      className={`w-7 h-7 flex items-center justify-center cursor-pointer transition-colors rounded-lg hover:bg-red-50 ${textMuted} hover:text-red-500`}>
                      <i className="ri-delete-bin-line text-sm"></i>
                    </button>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <p className={`${textMuted} text-xs mb-2`}>모니터링 키워드</p>
                    <div className="flex flex-wrap gap-1.5">
                      {setting.keywords.map(kw => <span key={kw} className={`text-xs px-2.5 py-1 rounded-full border ${tagDefault}`}>{kw}</span>)}
                    </div>
                  </div>
                  <div>
                    <p className={`${textMuted} text-xs mb-2`}>미디어 ({setting.media.length}개)</p>
                    <div className="flex flex-wrap gap-1.5">
                      {setting.media.slice(0, 4).map(m => {
                        const found = MEDIA_CATEGORIES.flatMap(c => c.items).find(i => i.id === m);
                        return found ? <span key={m} className={`text-xs px-2.5 py-1 rounded-full border ${tagDefault}`}>{found.label}</span> : null;
                      })}
                      {setting.media.length > 4 && <span className={`text-xs px-2.5 py-1 rounded-full ${textMuted} border`}>+{setting.media.length - 4}개</span>}
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}