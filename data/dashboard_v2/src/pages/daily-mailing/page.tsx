import { useEffect, useState } from 'react';
import {
  listMailSubscriptions, createMailSubscription, updateMailSubscription,
  deleteMailSubscription, testSendMailSubscription, previewMailSubscription, previewAdHoc,
  type MailSubscription, type MailPreview,
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
  const [smtpConfigured, setSmtpConfigured] = useState(false);
  const [submitStatus, setSubmitStatus] = useState<'idle' | 'success' | 'error'>('idle');
  const [submitMessage, setSubmitMessage] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<'new' | 'saved'>('new');
  const [listLoading, setListLoading] = useState(true);
  const [testingId, setTestingId] = useState<number | null>(null);
  const [preview, setPreview] = useState<MailPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewLabel, setPreviewLabel] = useState<string>('');

  const reload = async () => {
    try {
      const r = await listMailSubscriptions();
      setSavedSettings(r.items);
      setSmtpConfigured(r.smtp_configured);
    } catch {
      // 미인증 등은 ProtectedRoute 로 처리
    } finally {
      setListLoading(false);
    }
  };

  useEffect(() => {
    reload();
  }, []);

  const toggleKeyword = (kw: string) => {
    setSelectedKeywords(prev =>
      prev.includes(kw) ? prev.filter(k => k !== kw) : [...prev, kw]
    );
  };

  const addCustomKeyword = () => {
    const trimmed = customKeyword.trim();
    if (trimmed && !selectedKeywords.includes(trimmed)) {
      setSelectedKeywords(prev => [...prev, trimmed]);
      setCustomKeyword('');
    }
  };

  const removeKeyword = (kw: string) => {
    setSelectedKeywords(prev => prev.filter(k => k !== kw));
  };

  const toggleMedia = (id: string) => {
    setSelectedMedia(prev =>
      prev.includes(id) ? prev.filter(m => m !== id) : [...prev, id]
    );
  };

  const toggleCategoryMedia = (items: { id: string }[]) => {
    const ids = items.map(i => i.id);
    const allSelected = ids.every(id => selectedMedia.includes(id));
    if (allSelected) {
      setSelectedMedia(prev => prev.filter(m => !ids.includes(m)));
    } else {
      setSelectedMedia(prev => [...new Set([...prev, ...ids])]);
    }
  };

  const addEmail = () => {
    const trimmed = emailInput.trim();
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (trimmed && emailRegex.test(trimmed) && !emailList.includes(trimmed)) {
      setEmailList(prev => [...prev, trimmed]);
      setEmailInput('');
    }
  };

  const removeEmail = (email: string) => {
    setEmailList(prev => prev.filter(e => e !== email));
  };

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

  const handlePreview = async (mode: 'saved' | 'new', id?: number) => {
    setPreviewError(null);
    setPreview(null);
    setPreviewLoading(true);
    try {
      if (mode === 'saved' && id !== undefined) {
        const setting = savedSettings.find(s => s.id === id);
        setPreviewLabel(setting?.name ?? '저장된 설정');
        const r = await previewMailSubscription(id);
        setPreview(r);
      } else {
        const label = settingName.trim() || '새 메일링 설정';
        setPreviewLabel(label);
        const r = await previewAdHoc(label, selectedKeywords, selectedMedia);
        setPreview(r);
      }
    } catch (e) {
      setPreviewError(e instanceof Error ? e.message : '프리뷰 실패');
    } finally {
      setPreviewLoading(false);
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
      setSubmitMessage('메일링 설정이 저장되었습니다.');
      setSettingName('');
      await reload();
      setTimeout(() => { setSubmitStatus('idle'); setSubmitMessage(null); }, 3000);
    } catch (err) {
      setSubmitStatus('error');
      setSubmitMessage(err instanceof Error ? err.message : '저장 실패');
      setTimeout(() => { setSubmitStatus('idle'); setSubmitMessage(null); }, 4000);
    }
  };

  return (
    <div className="min-h-screen bg-[#0D1117] text-white">
      {/* Header */}
      <div className="px-8 pt-8 pb-6 border-b border-[#1E2530]">
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="w-5 h-5 flex items-center justify-center"><i className="ri-mail-settings-line text-[#00E5CC]"></i></span>
              <h1 className="text-2xl font-bold text-white">Daily Mailing Setting</h1>
            </div>
            <p className="text-[#8B9BB4] text-sm">키워드 모니터링 및 자동 메일 발송 스케줄 설정</p>
          </div>
          <div className="flex items-center gap-1 bg-[#161B27] border border-[#1E2530] rounded-lg p-1">
            {[
              { key: 'new', label: '새 설정', icon: 'ri-add-circle-line' },
              { key: 'saved', label: '저장된 설정', icon: 'ri-list-settings-line' },
            ].map(tab => (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key as 'new' | 'saved')}
                className={`flex items-center gap-1.5 px-4 py-1.5 rounded-md text-xs font-medium cursor-pointer whitespace-nowrap transition-all ${
                  activeTab === tab.key ? 'bg-[#00E5CC] text-[#0A0E1A]' : 'text-[#8B9BB4] hover:text-white'
                }`}
              >
                <span className="w-3.5 h-3.5 flex items-center justify-center"><i className={`${tab.icon} text-xs`}></i></span>
                {tab.label}
                {tab.key === 'saved' && (
                  <span className="bg-[#00E5CC]/20 text-[#00E5CC] text-xs px-1.5 py-0.5 rounded-full ml-1">{savedSettings.length}</span>
                )}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="px-8 py-6">
        {/* Success / Error Banner */}
        {submitStatus === 'success' && (
          <div className="mb-5 flex items-center gap-3 bg-[#00E5CC]/10 border border-[#00E5CC]/30 rounded-xl px-5 py-3">
            <span className="w-5 h-5 flex items-center justify-center text-[#00E5CC]"><i className="ri-checkbox-circle-line text-lg"></i></span>
            <p className="text-[#00E5CC] text-sm font-medium">{submitMessage ?? '메일링 설정이 저장되었습니다.'}</p>
          </div>
        )}
        {submitStatus === 'error' && (
          <div className="mb-5 flex items-center gap-3 bg-red-500/10 border border-red-500/30 rounded-xl px-5 py-3">
            <span className="w-5 h-5 flex items-center justify-center text-red-400"><i className="ri-error-warning-line text-lg"></i></span>
            <p className="text-red-400 text-sm font-medium">{submitMessage ?? '저장 중 오류가 발생했습니다.'}</p>
          </div>
        )}
        {!smtpConfigured && (
          <div className="mb-5 flex items-center gap-3 bg-[#F59E0B]/10 border border-[#F59E0B]/30 rounded-xl px-5 py-3">
            <span className="w-5 h-5 flex items-center justify-center text-[#F59E0B]"><i className="ri-information-line text-lg"></i></span>
            <p className="text-[#F59E0B] text-xs">
              SMTP 미설정 상태 — 설정은 저장되지만 실제 메일 발송은 config/.env 에 SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASSWORD/MAIL_FROM 를 추가한 뒤 가능합니다. (테스트 발송은 dry-run 으로 응답)
            </p>
          </div>
        )}

        {activeTab === 'new' && (
          <form
            data-readdy-form
            id="daily-mailing-form"
            onSubmit={handleSubmit}
            className="space-y-5"
          >
            {/* Setting Name */}
            <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-6">
              <h3 className="text-white font-bold text-sm mb-4 flex items-center gap-2">
                <span className="w-5 h-5 flex items-center justify-center text-[#00E5CC]"><i className="ri-bookmark-line text-sm"></i></span>
                설정 이름
              </h3>
              <input
                type="text"
                name="settingName"
                placeholder="예: 약가 정책 모니터링, 경쟁사 동향 추적..."
                value={settingName}
                onChange={e => setSettingName(e.target.value)}
                className="w-full bg-[#0D1117] border border-[#1E2530] rounded-xl px-4 py-3 text-white text-sm placeholder-[#4A5568] focus:outline-none focus:border-[#00E5CC]/50 transition-colors"
              />
            </div>

            {/* Keywords */}
            <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-6">
              <h3 className="text-white font-bold text-sm mb-1 flex items-center gap-2">
                <span className="w-5 h-5 flex items-center justify-center text-[#00E5CC]"><i className="ri-price-tag-3-line text-sm"></i></span>
                모니터링 키워드
              </h3>
              <p className="text-[#8B9BB4] text-xs mb-4">프리셋에서 선택하거나 직접 입력하세요</p>

              {/* Selected Keywords */}
              {selectedKeywords.length > 0 && (
                <div className="flex flex-wrap gap-2 mb-4 p-3 bg-[#0D1117] rounded-xl border border-[#1E2530]">
                  {selectedKeywords.map(kw => (
                    <span key={kw} className="flex items-center gap-1.5 bg-[#00E5CC]/15 border border-[#00E5CC]/30 text-[#00E5CC] text-xs px-3 py-1.5 rounded-full">
                      {kw}
                      <button type="button" onClick={() => removeKeyword(kw)} className="w-3.5 h-3.5 flex items-center justify-center hover:text-white cursor-pointer transition-colors">
                        <i className="ri-close-line text-xs"></i>
                      </button>
                    </span>
                  ))}
                </div>
              )}

              {/* Preset Keywords */}
              <div className="flex flex-wrap gap-2 mb-4">
                {PRESET_KEYWORDS.map(kw => (
                  <button
                    type="button"
                    key={kw}
                    onClick={() => toggleKeyword(kw)}
                    className={`px-3 py-1.5 rounded-full text-xs font-medium cursor-pointer whitespace-nowrap transition-all ${
                      selectedKeywords.includes(kw)
                        ? 'bg-[#00E5CC]/15 border border-[#00E5CC]/40 text-[#00E5CC]'
                        : 'bg-[#0D1117] border border-[#1E2530] text-[#8B9BB4] hover:text-white hover:border-[#2A3545]'
                    }`}
                  >
                    {selectedKeywords.includes(kw) && <i className="ri-check-line mr-1 text-xs"></i>}
                    {kw}
                  </button>
                ))}
              </div>

              {/* Custom Keyword Input */}
              <div className="flex gap-2">
                <input
                  type="text"
                  placeholder="직접 키워드 입력..."
                  value={customKeyword}
                  onChange={e => setCustomKeyword(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && (e.preventDefault(), addCustomKeyword())}
                  className="flex-1 bg-[#0D1117] border border-[#1E2530] rounded-xl px-4 py-2.5 text-white text-sm placeholder-[#4A5568] focus:outline-none focus:border-[#00E5CC]/50 transition-colors"
                />
                <button
                  type="button"
                  onClick={addCustomKeyword}
                  className="flex items-center gap-2 bg-[#00E5CC]/10 border border-[#00E5CC]/30 text-[#00E5CC] text-sm font-medium px-4 py-2.5 rounded-xl cursor-pointer whitespace-nowrap hover:bg-[#00E5CC]/20 transition-colors"
                >
                  <span className="w-4 h-4 flex items-center justify-center"><i className="ri-add-line text-sm"></i></span>
                  추가
                </button>
              </div>
            </div>

            {/* Media Selection */}
            <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-6">
              <h3 className="text-white font-bold text-sm mb-1 flex items-center gap-2">
                <span className="w-5 h-5 flex items-center justify-center text-[#00E5CC]"><i className="ri-newspaper-line text-sm"></i></span>
                모니터링 미디어
              </h3>
              <p className="text-[#8B9BB4] text-xs mb-4">모니터링할 미디어를 선택하세요 (복수 선택 가능)</p>

              <div className="grid grid-cols-2 gap-4">
                {MEDIA_CATEGORIES.map(cat => {
                  const allSelected = cat.items.every(i => selectedMedia.includes(i.id));
                  const someSelected = cat.items.some(i => selectedMedia.includes(i.id));
                  return (
                    <div key={cat.category} className="bg-[#0D1117] rounded-xl border border-[#1E2530] p-4">
                      <div className="flex items-center justify-between mb-3">
                        <span className="text-white text-xs font-bold">{cat.category}</span>
                        <button
                          type="button"
                          onClick={() => toggleCategoryMedia(cat.items)}
                          className={`text-xs px-2.5 py-1 rounded-full cursor-pointer whitespace-nowrap transition-all ${
                            allSelected
                              ? 'bg-[#00E5CC]/15 text-[#00E5CC] border border-[#00E5CC]/30'
                              : someSelected
                              ? 'bg-[#F59E0B]/15 text-[#F59E0B] border border-[#F59E0B]/30'
                              : 'bg-[#161B27] text-[#8B9BB4] border border-[#1E2530] hover:text-white'
                          }`}
                        >
                          {allSelected ? '전체 해제' : '전체 선택'}
                        </button>
                      </div>
                      <div className="space-y-2">
                        {cat.items.map(item => (
                          <label key={item.id} className="flex items-center gap-2.5 cursor-pointer group">
                            <div
                              onClick={() => toggleMedia(item.id)}
                              className={`w-4 h-4 rounded flex items-center justify-center flex-shrink-0 border transition-all cursor-pointer ${
                                selectedMedia.includes(item.id)
                                  ? 'bg-[#00E5CC] border-[#00E5CC]'
                                  : 'border-[#2A3545] group-hover:border-[#00E5CC]/50'
                              }`}
                            >
                              {selectedMedia.includes(item.id) && (
                                <i className="ri-check-line text-[#0A0E1A] text-xs"></i>
                              )}
                            </div>
                            <span
                              onClick={() => toggleMedia(item.id)}
                              className={`text-xs transition-colors ${selectedMedia.includes(item.id) ? 'text-white' : 'text-[#8B9BB4] group-hover:text-white'}`}
                            >
                              {item.label}
                            </span>
                          </label>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>

              <div className="mt-3 flex items-center gap-2">
                <span className="text-[#8B9BB4] text-xs">선택된 미디어:</span>
                <span className="text-[#00E5CC] text-xs font-bold">{selectedMedia.length}개</span>
              </div>
            </div>

            {/* Schedule */}
            <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-6">
              <h3 className="text-white font-bold text-sm mb-1 flex items-center gap-2">
                <span className="w-5 h-5 flex items-center justify-center text-[#00E5CC]"><i className="ri-calendar-schedule-line text-sm"></i></span>
                발송 스케줄
              </h3>
              <p className="text-[#8B9BB4] text-xs mb-4">메일 발송 주기와 시간을 설정하세요</p>

              <div className="flex items-start gap-6">
                {/* Daily / Weekly Toggle */}
                <div className="flex items-center gap-1 bg-[#0D1117] border border-[#1E2530] rounded-xl p-1">
                  {(['Daily', 'Weekly'] as const).map(s => (
                    <button
                      type="button"
                      key={s}
                      onClick={() => setSchedule(s)}
                      className={`px-5 py-2 rounded-lg text-sm font-semibold cursor-pointer whitespace-nowrap transition-all ${
                        schedule === s ? 'bg-[#00E5CC] text-[#0A0E1A]' : 'text-[#8B9BB4] hover:text-white'
                      }`}
                    >
                      {s}
                    </button>
                  ))}
                </div>

                {/* Weekly Day Selector */}
                {schedule === 'Weekly' && (
                  <div className="flex items-center gap-2 flex-wrap">
                    {['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'].map(day => (
                      <button
                        type="button"
                        key={day}
                        onClick={() => setWeekDay(day)}
                        className={`px-3 py-2 rounded-lg text-xs font-medium cursor-pointer whitespace-nowrap transition-all ${
                          weekDay === day
                            ? 'bg-[#00E5CC]/15 border border-[#00E5CC]/40 text-[#00E5CC]'
                            : 'bg-[#0D1117] border border-[#1E2530] text-[#8B9BB4] hover:text-white'
                        }`}
                      >
                        {day.slice(0, 3)}
                      </button>
                    ))}
                  </div>
                )}

                {/* Time Picker */}
                <div className="flex items-center gap-3">
                  <span className="w-4 h-4 flex items-center justify-center text-[#8B9BB4]">
                    <i className="ri-time-line text-sm"></i>
                  </span>
                  <select
                    name="scheduleTime"
                    value={scheduleTime}
                    onChange={e => setScheduleTime(e.target.value)}
                    className="bg-[#0D1117] border border-[#1E2530] rounded-xl px-4 py-2 text-white text-sm focus:outline-none focus:border-[#00E5CC]/50 cursor-pointer transition-colors"
                  >
                    {['06:00','07:00','07:30','08:00','08:30','09:00','09:30','10:00','12:00','18:00','21:00'].map(t => (
                      <option key={t} value={t}>{t}</option>
                    ))}
                  </select>
                </div>
              </div>

              {/* Schedule Preview */}
              <div className="mt-4 flex items-center gap-2 bg-[#0D1117] rounded-xl px-4 py-3 border border-[#1E2530]">
                <span className="w-4 h-4 flex items-center justify-center text-[#00E5CC]"><i className="ri-information-line text-sm"></i></span>
                <p className="text-[#8B9BB4] text-xs">
                  {schedule === 'Daily'
                    ? `매일 ${scheduleTime}에 메일이 발송됩니다`
                    : `매주 ${weekDay} ${scheduleTime}에 메일이 발송됩니다`}
                </p>
              </div>
            </div>

            {/* Email Recipients */}
            <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-6">
              <h3 className="text-white font-bold text-sm mb-1 flex items-center gap-2">
                <span className="w-5 h-5 flex items-center justify-center text-[#00E5CC]"><i className="ri-mail-line text-sm"></i></span>
                수신 이메일
              </h3>
              <p className="text-[#8B9BB4] text-xs mb-4">메일을 수신할 이메일 주소를 입력하세요 (복수 추가 가능)</p>

              {/* Email List */}
              {emailList.length > 0 && (
                <div className="flex flex-wrap gap-2 mb-4">
                  {emailList.map(email => (
                    <span key={email} className="flex items-center gap-2 bg-[#0D1117] border border-[#1E2530] text-white text-xs px-3 py-2 rounded-xl">
                      <span className="w-3.5 h-3.5 flex items-center justify-center text-[#00E5CC]"><i className="ri-mail-line text-xs"></i></span>
                      {email}
                      <button type="button" onClick={() => removeEmail(email)} className="w-3.5 h-3.5 flex items-center justify-center text-[#4A5568] hover:text-red-400 cursor-pointer transition-colors">
                        <i className="ri-close-line text-xs"></i>
                      </button>
                    </span>
                  ))}
                </div>
              )}

              <div className="flex gap-2">
                <input
                  type="email"
                  name="email"
                  placeholder="이메일 주소 입력..."
                  value={emailInput}
                  onChange={e => setEmailInput(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && (e.preventDefault(), addEmail())}
                  className="flex-1 bg-[#0D1117] border border-[#1E2530] rounded-xl px-4 py-2.5 text-white text-sm placeholder-[#4A5568] focus:outline-none focus:border-[#00E5CC]/50 transition-colors"
                />
                <button
                  type="button"
                  onClick={addEmail}
                  className="flex items-center gap-2 bg-[#00E5CC]/10 border border-[#00E5CC]/30 text-[#00E5CC] text-sm font-medium px-4 py-2.5 rounded-xl cursor-pointer whitespace-nowrap hover:bg-[#00E5CC]/20 transition-colors"
                >
                  <span className="w-4 h-4 flex items-center justify-center"><i className="ri-add-line text-sm"></i></span>
                  추가
                </button>
              </div>
            </div>

            {/* Submit */}
            <div className="flex items-center justify-between bg-[#161B27] rounded-2xl border border-[#1E2530] p-5">
              <div className="flex items-center gap-4 text-xs text-[#8B9BB4]">
                <span className="flex items-center gap-1.5">
                  <span className="w-3.5 h-3.5 flex items-center justify-center text-[#00E5CC]"><i className="ri-price-tag-3-line text-xs"></i></span>
                  키워드 {selectedKeywords.length}개
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="w-3.5 h-3.5 flex items-center justify-center text-[#00E5CC]"><i className="ri-newspaper-line text-xs"></i></span>
                  미디어 {selectedMedia.length}개
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="w-3.5 h-3.5 flex items-center justify-center text-[#00E5CC]"><i className="ri-mail-line text-xs"></i></span>
                  수신자 {emailList.length}명
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="w-3.5 h-3.5 flex items-center justify-center text-[#00E5CC]"><i className="ri-time-line text-xs"></i></span>
                  {schedule} {scheduleTime}
                </span>
              </div>
              <button
                type="button"
                onClick={() => handlePreview('new')}
                disabled={previewLoading}
                className="flex items-center gap-2 bg-[#0D1117] border border-[#00E5CC]/40 text-[#00E5CC] text-sm font-medium px-5 py-2.5 rounded-xl cursor-pointer whitespace-nowrap hover:bg-[#00E5CC]/10 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <span className="w-4 h-4 flex items-center justify-center"><i className={previewLoading ? 'ri-loader-4-line animate-spin text-sm' : 'ri-eye-line text-sm'}></i></span>
                프리뷰
              </button>
              <button
                type="submit"
                disabled={selectedKeywords.length === 0 || selectedMedia.length === 0 || emailList.length === 0}
                className="flex items-center gap-2 bg-[#00E5CC] text-[#0A0E1A] text-sm font-bold px-6 py-2.5 rounded-xl cursor-pointer whitespace-nowrap hover:bg-[#00C9B1] transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <span className="w-4 h-4 flex items-center justify-center"><i className="ri-save-line text-sm"></i></span>
                설정 저장
              </button>
            </div>
          </form>
        )}

        {/* Saved Settings Tab */}
        {activeTab === 'saved' && (
          <div className="space-y-4">
            {listLoading && (
              <div className="text-center py-16 text-[#8B9BB4] text-sm"><i className="ri-loader-4-line animate-spin mr-2"></i>설정 로드 중…</div>
            )}
            {!listLoading && savedSettings.length === 0 && (
              <div className="text-center py-16 text-[#4A5568]">
                <span className="w-12 h-12 flex items-center justify-center mx-auto mb-3"><i className="ri-mail-settings-line text-4xl"></i></span>
                <p className="text-sm">저장된 설정이 없습니다</p>
                <button
                  onClick={() => setActiveTab('new')}
                  className="mt-4 text-[#00E5CC] text-sm cursor-pointer hover:underline"
                >
                  새 설정 만들기
                </button>
              </div>
            )}
            {savedSettings.map(setting => (
              <div key={setting.id} className={`bg-[#161B27] rounded-2xl border p-5 transition-all ${setting.active ? 'border-[#1E2530]' : 'border-[#1E2530] opacity-60'}`}>
                <div className="flex items-start justify-between mb-4">
                  <div className="flex items-center gap-3">
                    <div className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${setting.active ? 'bg-[#00E5CC]' : 'bg-[#4A5568]'}`}></div>
                    <div>
                      <h4 className="text-white font-bold text-sm">{setting.name}</h4>
                      <p className="text-[#8B9BB4] text-xs mt-0.5">
                        {setting.emails.slice(0, 2).join(', ')}
                        {setting.emails.length > 2 ? ` +${setting.emails.length - 2}명` : ''}
                      </p>
                      {setting.last_sent_at && (
                        <p className="text-[#4A5568] text-[10px] mt-0.5">마지막 발송 {new Date(setting.last_sent_at).toLocaleString('ko-KR')}</p>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className={`text-xs px-2.5 py-1 rounded-full font-medium ${setting.schedule === 'Daily' ? 'bg-[#00E5CC]/10 text-[#00E5CC]' : 'bg-[#F59E0B]/10 text-[#F59E0B]'}`}>
                      {setting.schedule}
                      {setting.schedule === 'Weekly' && setting.weekDay ? ` ${setting.weekDay.slice(0, 3)}` : ''}{' '}{setting.time}
                    </span>
                    <button
                      onClick={() => handlePreview('saved', setting.id)}
                      className="text-xs px-3 py-1 rounded-full cursor-pointer whitespace-nowrap transition-all border border-[#8B9BB4]/30 text-[#8B9BB4] hover:bg-[#8B9BB4]/10"
                    >
                      프리뷰
                    </button>
                    <button
                      onClick={() => handleTestSend(setting.id)}
                      disabled={testingId === setting.id}
                      className="text-xs px-3 py-1 rounded-full cursor-pointer whitespace-nowrap transition-all border border-[#00E5CC]/30 text-[#00E5CC] hover:bg-[#00E5CC]/10 disabled:opacity-50"
                    >
                      {testingId === setting.id ? '발송 중…' : '테스트 발송'}
                    </button>
                    <button
                      onClick={() => toggleSetting(setting.id, !setting.active)}
                      className={`text-xs px-3 py-1 rounded-full cursor-pointer whitespace-nowrap transition-all border ${
                        setting.active
                          ? 'border-[#EF4444]/30 text-[#EF4444] hover:bg-[#EF4444]/10'
                          : 'border-[#00E5CC]/30 text-[#00E5CC] hover:bg-[#00E5CC]/10'
                      }`}
                    >
                      {setting.active ? '비활성화' : '활성화'}
                    </button>
                    <button
                      onClick={() => deleteSetting(setting.id)}
                      className="w-7 h-7 flex items-center justify-center text-[#4A5568] hover:text-red-400 cursor-pointer transition-colors rounded-lg hover:bg-red-400/10"
                    >
                      <i className="ri-delete-bin-line text-sm"></i>
                    </button>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <p className="text-[#4A5568] text-xs mb-2">모니터링 키워드</p>
                    <div className="flex flex-wrap gap-1.5">
                      {setting.keywords.map(kw => (
                        <span key={kw} className="bg-[#0D1117] border border-[#1E2530] text-[#8B9BB4] text-xs px-2.5 py-1 rounded-full">{kw}</span>
                      ))}
                    </div>
                  </div>
                  <div>
                    <p className="text-[#4A5568] text-xs mb-2">미디어 ({setting.media.length}개)</p>
                    <div className="flex flex-wrap gap-1.5">
                      {setting.media.slice(0, 4).map(m => {
                        const found = MEDIA_CATEGORIES.flatMap(c => c.items).find(i => i.id === m);
                        return found ? (
                          <span key={m} className="bg-[#0D1117] border border-[#1E2530] text-[#8B9BB4] text-xs px-2.5 py-1 rounded-full">{found.label}</span>
                        ) : null;
                      })}
                      {setting.media.length > 4 && (
                        <span className="bg-[#0D1117] border border-[#1E2530] text-[#4A5568] text-xs px-2.5 py-1 rounded-full">+{setting.media.length - 4}개</span>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Preview Modal */}
      {(preview || previewLoading || previewError) && (
        <div
          className="fixed inset-0 z-50 bg-[#0D1117]/80 backdrop-blur-sm flex items-center justify-center p-6"
          onClick={() => { setPreview(null); setPreviewError(null); }}
        >
          <div
            className="bg-[#161B27] border border-[#1E2530] rounded-2xl w-full max-w-3xl max-h-[90vh] flex flex-col overflow-hidden"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-center gap-3 px-5 py-4 border-b border-[#1E2530]">
              <span className="w-5 h-5 flex items-center justify-center text-[#00E5CC]"><i className="ri-mail-line text-base"></i></span>
              <div className="flex-1 min-w-0">
                <h3 className="text-white font-bold text-sm truncate">{preview?.subject ?? `프리뷰 — ${previewLabel}`}</h3>
                <p className="text-[#8B9BB4] text-xs">실제 발송될 HTML 메일 본문 미리보기</p>
              </div>
              <button
                onClick={() => { setPreview(null); setPreviewError(null); }}
                className="w-8 h-8 flex items-center justify-center text-[#8B9BB4] hover:text-white hover:bg-[#1E2530] rounded-lg cursor-pointer transition-colors"
              >
                <i className="ri-close-line text-lg"></i>
              </button>
            </div>
            <div className="flex-1 overflow-hidden bg-[#0D1117]">
              {previewLoading && (
                <div className="h-full flex items-center justify-center text-[#8B9BB4] text-sm">
                  <i className="ri-loader-4-line animate-spin mr-2"></i>프리뷰 생성 중…
                </div>
              )}
              {previewError && (
                <div className="h-full flex items-center justify-center text-red-400 text-sm px-6 text-center">
                  {previewError}
                </div>
              )}
              {preview && (
                <iframe
                  srcDoc={preview.html}
                  title="Daily Mailing Preview"
                  className="w-full h-[70vh] border-0 bg-white"
                />
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
