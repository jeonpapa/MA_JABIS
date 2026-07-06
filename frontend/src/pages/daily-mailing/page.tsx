import { useEffect, useState } from 'react';
import {
  listMailSubscriptions, createMailSubscription, updateMailSubscription,
  deleteMailSubscription, testSendMailSubscription,
  requestTestMail,
  type MailSubscription,
} from '@/api/mailSubscriptions';

interface PreviewModalState {
  subscriptionName: string;
  subject: string;
  html: string;
}

// IME(한글) 조합 중 Enter 는 무시 — 조합 미완성 음절(예: "윈레브에어"의 "어")이
// 별도 chip 으로 등록되는 버그 방지. 모든 chip 입력의 Enter 커밋은 이 헬퍼를 사용한다.
const onEnterCommit = (fn: () => void) => (e: React.KeyboardEvent<HTMLInputElement>) => {
  if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
    e.preventDefault();
    fn();
  }
};

// 스콥 프리셋 — 각 용어는 정확히 한 그룹에만 존재한다 (그룹 간 중복 금지).
// 정책·제도·기관 용어는 PRESET_POLICY_TOPICS, 치료분야 용어는 PRESET_DISEASE_AREAS 로.
const PRESET_KEYWORDS = [
  '약가 인하', '급여 등재', '보험 적용', '임상시험', '허가 승인',
  '파이프라인', '바이오시밀러', '제네릭', '실거래가',
];

// 한국 매체 모니터링이므로 프리셋은 한국어 표기 우선 (영문 표기는 Naver 검색 리콜이 낮음)
const PRESET_BRANDS = ['키트루다', '가다실', '린파자', '웰리렉', '브리디온', '에멘드'];
const PRESET_COMPANIES = ['MSD', '한국MSD'];
const PRESET_POLICY_TOPICS = [
  '약평위', '암질심', '건정심', '약가협상', '위험분담제', 'RSA',
  '사용량-약가 연동', '선별급여', '비급여', 'HTA',
  '심평원', '건강보험공단', '보건복지부',
];
const PRESET_DISEASE_AREAS = ['항암제', '면역항암제', '표적치료제', '백신'];

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
  const [selectedKeywords, setSelectedKeywords] = useState<string[]>(['약가 인하', '급여 등재']);
  const [customKeyword, setCustomKeyword] = useState('');
  const [selectedMedia, setSelectedMedia] = useState<string[]>(['medi', 'yakup', 'hankyung']);
  const [selectedBrands, setSelectedBrands] = useState<string[]>([]);
  const [customBrand, setCustomBrand] = useState('');
  const [selectedCompanies, setSelectedCompanies] = useState<string[]>([]);
  const [customCompany, setCustomCompany] = useState('');
  const [selectedPolicyTopics, setSelectedPolicyTopics] = useState<string[]>([]);
  const [customPolicyTopic, setCustomPolicyTopic] = useState('');
  const [selectedDiseaseAreas, setSelectedDiseaseAreas] = useState<string[]>([]);
  const [customDiseaseArea, setCustomDiseaseArea] = useState('');
  const [customSources, setCustomSources] = useState<{ url: string; name?: string }[]>([]);
  const [customSourceUrl, setCustomSourceUrl] = useState('');
  const [customSourceName, setCustomSourceName] = useState('');
  const [customSourceError, setCustomSourceError] = useState<string | null>(null);
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
  const [testRequestId, setTestRequestId] = useState<number | null>(null);
  const [testRequestNote, setTestRequestNote] = useState<{ id: number; message: string; ok: boolean } | null>(null);
  const [activeTab, setActiveTab] = useState<'new' | 'saved'>('new');
  const [previewModal, setPreviewModal] = useState<PreviewModalState | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);

  // 크로스필드 dedupe: 구조화 그룹(브랜드/회사/정책/질환)에서 선택한 용어는
  // 자유 키워드로 중복 저장하지 않는다. 저장 가능 조건 = 스콥 전체에 1개 이상의 용어.
  const structuredScopeTerms = new Set([
    ...selectedBrands, ...selectedCompanies, ...selectedPolicyTopics, ...selectedDiseaseAreas,
  ]);
  const dedupedKeywords = selectedKeywords.filter(k => !structuredScopeTerms.has(k));
  const scopeTermCount = dedupedKeywords.length + selectedBrands.length + selectedCompanies.length
    + selectedPolicyTopics.length + selectedDiseaseAreas.length;

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
  const toggleBrand = (v: string) => { setSelectedBrands(prev => prev.includes(v) ? prev.filter(x => x !== v) : [...prev, v]); };
  const removeBrand = (v: string) => { setSelectedBrands(prev => prev.filter(x => x !== v)); };
  const addCustomBrand = () => {
    const trimmed = customBrand.trim();
    if (trimmed && !selectedBrands.includes(trimmed)) { setSelectedBrands(prev => [...prev, trimmed]); setCustomBrand(''); }
  };
  const toggleCompany = (v: string) => { setSelectedCompanies(prev => prev.includes(v) ? prev.filter(x => x !== v) : [...prev, v]); };
  const removeCompany = (v: string) => { setSelectedCompanies(prev => prev.filter(x => x !== v)); };
  const addCustomCompany = () => {
    const trimmed = customCompany.trim();
    if (trimmed && !selectedCompanies.includes(trimmed)) { setSelectedCompanies(prev => [...prev, trimmed]); setCustomCompany(''); }
  };
  const togglePolicyTopic = (v: string) => { setSelectedPolicyTopics(prev => prev.includes(v) ? prev.filter(x => x !== v) : [...prev, v]); };
  const removePolicyTopic = (v: string) => { setSelectedPolicyTopics(prev => prev.filter(x => x !== v)); };
  const addCustomPolicyTopic = () => {
    const trimmed = customPolicyTopic.trim();
    if (trimmed && !selectedPolicyTopics.includes(trimmed)) { setSelectedPolicyTopics(prev => [...prev, trimmed]); setCustomPolicyTopic(''); }
  };
  const toggleDiseaseArea = (v: string) => { setSelectedDiseaseAreas(prev => prev.includes(v) ? prev.filter(x => x !== v) : [...prev, v]); };
  const removeDiseaseArea = (v: string) => { setSelectedDiseaseAreas(prev => prev.filter(x => x !== v)); };
  const addCustomDiseaseArea = () => {
    const trimmed = customDiseaseArea.trim();
    if (trimmed && !selectedDiseaseAreas.includes(trimmed)) { setSelectedDiseaseAreas(prev => [...prev, trimmed]); setCustomDiseaseArea(''); }
  };
  const addCustomSource = () => {
    const trimmed = customSourceUrl.trim();
    if (!trimmed) return;
    if (!/^https?:\/\//i.test(trimmed)) {
      setCustomSourceError('http(s):// 로 시작하는 URL을 입력하세요');
      return;
    }
    if (customSources.some(s => s.url === trimmed)) {
      setCustomSourceError('이미 추가된 URL입니다');
      return;
    }
    setCustomSourceError(null);
    const name = customSourceName.trim();
    setCustomSources(prev => [...prev, name ? { url: trimmed, name } : { url: trimmed }]);
    setCustomSourceUrl('');
    setCustomSourceName('');
  };
  const removeCustomSource = (url: string) => { setCustomSources(prev => prev.filter(s => s.url !== url)); };
  const sourceDomain = (url: string) => {
    try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return url; }
  };
  const addEmail = () => {
    const trimmed = emailInput.trim();
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (trimmed && emailRegex.test(trimmed) && !emailList.includes(trimmed)) { setEmailList(prev => [...prev, trimmed]); setEmailInput(''); }
  };
  const removeEmail = (email: string) => { setEmailList(prev => prev.filter(e => e !== email)); };

  const resetForm = () => {
    setSettingName('');
    setSelectedKeywords(['약가 인하', '급여 등재']);
    setCustomKeyword('');
    setSelectedMedia(['medi', 'yakup', 'hankyung']);
    setSelectedBrands([]); setCustomBrand('');
    setSelectedCompanies([]); setCustomCompany('');
    setSelectedPolicyTopics([]); setCustomPolicyTopic('');
    setSelectedDiseaseAreas([]); setCustomDiseaseArea('');
    setCustomSources([]); setCustomSourceUrl(''); setCustomSourceName(''); setCustomSourceError(null);
    setSchedule('Daily'); setScheduleTime('08:00'); setWeekDay('Monday');
    setEmailInput(''); setEmailList(['marketaccess@msd.com']);
  };

  // 저장된 설정 카드 → 편집 모드: 저장값 전체를 폼 state 로 복원 후 '새 설정' 탭으로 이동.
  const startEdit = (setting: MailSubscription) => {
    setEditingId(setting.id);
    setSettingName(setting.name);
    setSelectedKeywords(setting.keywords ?? []);
    setSelectedMedia(setting.media ?? []);
    setSelectedBrands(setting.brands ?? []);
    setSelectedCompanies(setting.companies ?? []);
    setSelectedPolicyTopics(setting.policy_topics ?? []);
    setSelectedDiseaseAreas(setting.disease_areas ?? []);
    setCustomSources(setting.custom_sources ?? []);
    setSchedule(setting.schedule);
    setScheduleTime(setting.time);
    setWeekDay(setting.weekDay ?? 'Monday');
    setEmailList(setting.emails ?? []);
    setCustomKeyword(''); setCustomBrand(''); setCustomCompany('');
    setCustomPolicyTopic(''); setCustomDiseaseArea('');
    setCustomSourceUrl(''); setCustomSourceName(''); setCustomSourceError(null);
    setEmailInput('');
    setActiveTab('new');
  };

  const cancelEdit = () => { setEditingId(null); resetForm(); };

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

  const handleTestSend = async (id: number, name: string) => {
    setTestingId(id);
    try {
      const r = await testSendMailSubscription(id);
      if (r.ok && r.mode === 'preview') {
        setPreviewModal({
          subscriptionName: name,
          subject: r.subject ?? '(제목 없음)',
          html: r.html ?? '<p>미리보기 HTML이 없습니다.</p>',
        });
      } else if (r.ok && r.mode === 'smtp') {
        alert(`발송 완료 → ${r.recipients.join(', ')}`);
      } else if (r.ok && r.mode === 'dry-run') {
        alert(`[Dry-run] SMTP 미설정. ${r.message ?? ''}`);
      } else if (r.ok && r.mode === 'none') {
        alert(r.message ?? '아직 헤르메스 발송 이력이 없습니다 — 실제 메일은 헤르메스가 작성·발송합니다.');
      } else {
        alert(`최근 발송 브리프 로드 실패: ${r.message ?? ''}`);
      }
      await reload();
    } catch (e) {
      alert(e instanceof Error ? e.message : '최근 발송 브리프 로드 실패');
    } finally {
      setTestingId(null);
    }
  };

  const handleRequestTestMail = async (id: number) => {
    if (!window.confirm('헤르메스에 테스트 메일을 요청할까요? 검토 후 [TEST] 메일이 발송됩니다.')) return;
    setTestRequestId(id);
    setTestRequestNote(null);
    try {
      const r = await requestTestMail(id);
      setTestRequestNote({ id, ok: r.ok, message: r.message ?? (r.ok ? '테스트 메일 요청이 접수되었습니다.' : '요청 실패') });
    } catch (e) {
      setTestRequestNote({ id, ok: false, message: e instanceof Error ? e.message : '테스트 메일 요청 실패' });
    } finally {
      setTestRequestId(null);
      setTimeout(() => setTestRequestNote(prev => (prev?.id === id ? null : prev)), 6000);
    }
  };

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (scopeTermCount === 0 || selectedMedia.length === 0 || emailList.length === 0) return;
    setSubmitting(true);
    const isEdit = editingId != null;
    try {
      // 생성/수정이 동일한 payload 빌더를 공유한다. 수정 시 active 는 건드리지 않는다
      // (비활성 상태의 설정을 편집해도 강제 재활성화되지 않도록).
      const payload = {
        name: settingName.trim() || '새 메일링 설정',
        keywords: dedupedKeywords,
        media: selectedMedia,
        schedule,
        time: scheduleTime,
        weekDay: schedule === 'Weekly' ? weekDay : null,
        emails: emailList,
        brands: selectedBrands,
        companies: selectedCompanies,
        policyTopics: selectedPolicyTopics,
        diseaseAreas: selectedDiseaseAreas,
        customSources,
      };
      if (isEdit) {
        await updateMailSubscription(editingId, payload);
      } else {
        await createMailSubscription({ ...payload, active: true });
      }
      setSubmitStatus('success');
      setSubmitMessage(isEdit
        ? '설정이 수정되었습니다. 헤르메스 에이전트가 다음 발송부터 수정된 스콥을 사용합니다.'
        : '모니터링 스콥이 저장되었습니다. 헤르메스 에이전트가 매일 이 스콥으로 검토·작성·발송합니다.');
      setEditingId(null);
      resetForm();
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

  const renderScopeGroup = (opts: {
    icon: string;
    title: string;
    hint: string;
    values: string[];
    presets: string[];
    onToggle: (v: string) => void;
    onRemove: (v: string) => void;
    customValue: string;
    onCustomChange: (v: string) => void;
    onAddCustom: () => void;
  }) => (
    <div className={`${cardBg} rounded-2xl border ${cardBorder} p-6`}>
      <h3 className={`font-bold text-sm mb-1 flex items-center gap-2 ${textMain}`}>
        <span className={`w-5 h-5 flex items-center justify-center ${accentColor}`}><i className={`${opts.icon} text-sm`}></i></span>
        {opts.title}
      </h3>
      <p className={`${textSub} text-xs mb-4`}>{opts.hint}</p>
      {opts.values.length > 0 && (
        <div className={`flex flex-wrap gap-2 mb-4 p-3 rounded-xl border ${divider}`}>
          {opts.values.map(v => (
            <span key={v} className={`flex items-center gap-1.5 border text-xs px-3 py-1.5 rounded-full ${tagSelected}`}>
              {v}
              <button type="button" onClick={() => opts.onRemove(v)} className="w-3.5 h-3.5 flex items-center justify-center hover:opacity-70 cursor-pointer transition-colors"><i className="ri-close-line text-xs"></i></button>
            </span>
          ))}
        </div>
      )}
      {opts.presets.length > 0 && (
        <div className="flex flex-wrap gap-2 mb-4">
          {opts.presets.map(v => (
            <button type="button" key={v} onClick={() => opts.onToggle(v)}
              className={`px-3 py-1.5 rounded-full text-xs font-medium cursor-pointer whitespace-nowrap transition-all ${opts.values.includes(v) ? tagSelected : tagDefault}`}>
              {opts.values.includes(v) && <i className="ri-check-line mr-1 text-xs"></i>}{v}
            </button>
          ))}
        </div>
      )}
      <div className="flex gap-2">
        <input type="text" placeholder="직접 입력..." value={opts.customValue} onChange={e => opts.onCustomChange(e.target.value)}
          onKeyDown={onEnterCommit(opts.onAddCustom)}
          className={`flex-1 rounded-xl px-4 py-2.5 text-sm focus:outline-none transition-colors ${inputBg} ${inputFocus} ${inputText}`} />
        <button type="button" onClick={opts.onAddCustom}
          className={`flex items-center gap-2 text-sm font-medium px-4 py-2.5 rounded-xl cursor-pointer whitespace-nowrap transition-colors border ${accentBg} ${accentBorder} ${accentColor} hover:opacity-80`}>
          <span className="w-4 h-4 flex items-center justify-center"><i className="ri-add-line text-sm"></i></span>추가
        </button>
      </div>
    </div>
  );

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
            <p className="text-teal-700 text-sm font-medium">{submitMessage ?? '모니터링 스콥이 저장되었습니다. 헤르메스 에이전트가 매일 이 스콥으로 검토·작성·발송합니다.'}</p>
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
            {editingId != null && (
              <div className={`flex items-center justify-between rounded-2xl border p-4 ${accentBg} ${accentBorder}`}>
                <p className={`text-xs font-medium flex items-center gap-1.5 ${accentColor}`}>
                  <i className="ri-edit-line text-sm"></i>
                  저장된 설정 수정 중 — "{settingName || '(이름 없음)'}" · 저장 시 기존 설정을 덮어씁니다
                </p>
                <button type="button" onClick={cancelEdit}
                  className={`text-xs px-3 py-1 rounded-full cursor-pointer whitespace-nowrap transition-all border ${isDark ? 'border-[#2A3545] text-[#8B9BB4] hover:text-white' : 'border-gray-300 text-gray-500 hover:text-gray-900'}`}>
                  취소
                </button>
              </div>
            )}
            {/* Setting Name */}
            <div className={`${cardBg} rounded-2xl border ${cardBorder} p-6`}>
              <h3 className={`font-bold text-sm mb-4 flex items-center gap-2 ${textMain}`}>
                <span className={`w-5 h-5 flex items-center justify-center ${accentColor}`}><i className="ri-bookmark-line text-sm"></i></span>
                설정 이름
              </h3>
              <input type="text" name="settingName" placeholder="예: 약가 정책 모니터링, MNC 동향 추적..." value={settingName} onChange={e => setSettingName(e.target.value)}
                className={`w-full rounded-xl px-4 py-3 text-sm focus:outline-none transition-colors ${inputBg} ${inputFocus} ${inputText}`} />
            </div>

            {/* 모니터링 스콥 — 무엇을 찾을지 (5개 그룹 통합 섹션) */}
            <div className="space-y-3">
              <div className={`rounded-2xl border p-5 ${accentBg} ${accentBorder}`}>
                <h2 className={`font-bold text-base flex items-center gap-2 ${textMain}`}>
                  <span className={`w-5 h-5 flex items-center justify-center ${accentColor}`}><i className="ri-crosshair-2-line"></i></span>
                  모니터링 스콥 — 무엇을 찾을지
                </h2>
                <p className={`${textSub} text-xs mt-1.5 leading-relaxed`}>
                  헤르메스가 검색·검토할 대상입니다. 브랜드 / 회사 / 정책·제도 / 질환 영역 / 자유 키워드 중 <span className={`font-semibold ${accentColor}`}>한 그룹 이상</span>에 항목이 있으면 저장할 수 있습니다.
                  같은 용어는 한 그룹에만 저장됩니다 (구조화 그룹 우선).
                </p>
              </div>

              {/* Brands */}
              {renderScopeGroup({
                icon: 'ri-capsule-line',
                title: '브랜드 — 제품명',
                hint: '모니터링할 제품(브랜드)명 (선택)',
                values: selectedBrands,
                presets: PRESET_BRANDS,
                onToggle: toggleBrand,
                onRemove: removeBrand,
                customValue: customBrand,
                onCustomChange: setCustomBrand,
                onAddCustom: addCustomBrand,
              })}

              {/* Companies */}
              {renderScopeGroup({
                icon: 'ri-building-2-line',
                title: '회사 — 제약사명',
                hint: '모니터링할 제약회사명 (선택)',
                values: selectedCompanies,
                presets: PRESET_COMPANIES,
                onToggle: toggleCompany,
                onRemove: removeCompany,
                customValue: customCompany,
                onCustomChange: setCustomCompany,
                onAddCustom: addCustomCompany,
              })}

              {/* Policy Topics */}
              {renderScopeGroup({
                icon: 'ri-government-line',
                title: '정책·제도 — 위원회·기관·급여 제도',
                hint: '약평위·암질심 등 위원회, 심평원 등 기관, 급여·약가 제도 용어 (선택)',
                values: selectedPolicyTopics,
                presets: PRESET_POLICY_TOPICS,
                onToggle: togglePolicyTopic,
                onRemove: removePolicyTopic,
                customValue: customPolicyTopic,
                onCustomChange: setCustomPolicyTopic,
                onAddCustom: addCustomPolicyTopic,
              })}

              {/* Disease Areas */}
              {renderScopeGroup({
                icon: 'ri-heart-pulse-line',
                title: '질환 영역 — 치료 분야',
                hint: '질환·치료 영역 (예: 항암제, 백신) (선택)',
                values: selectedDiseaseAreas,
                presets: PRESET_DISEASE_AREAS,
                onToggle: toggleDiseaseArea,
                onRemove: removeDiseaseArea,
                customValue: customDiseaseArea,
                onCustomChange: setCustomDiseaseArea,
                onAddCustom: addCustomDiseaseArea,
              })}

              {/* Free Keywords */}
              {renderScopeGroup({
                icon: 'ri-price-tag-3-line',
                title: '자유 키워드 (기타)',
                hint: '위 그룹에 속하지 않는 일반 키워드. 다른 그룹에서 이미 선택한 용어는 저장 시 여기서 자동 제외됩니다',
                values: selectedKeywords,
                presets: PRESET_KEYWORDS,
                onToggle: toggleKeyword,
                onRemove: removeKeyword,
                customValue: customKeyword,
                onCustomChange: setCustomKeyword,
                onAddCustom: addCustomKeyword,
              })}
            </div>

            {/* Media Selection */}
            <div className={`${cardBg} rounded-2xl border ${cardBorder} p-6`}>
              <h3 className={`font-bold text-sm mb-1 flex items-center gap-2 ${textMain}`}>
                <span className={`w-5 h-5 flex items-center justify-center ${accentColor}`}><i className="ri-newspaper-line text-sm"></i></span>
                모니터링 미디어 — 어디서 찾을지
              </h3>
              <p className={`${textSub} text-xs mb-4`}>위 스콥을 검색할 매체를 선택하세요 (스콥과 별개의 "검색 위치" 설정)</p>
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

              <div className={`mt-5 pt-5 border-t ${divider}`}>
                <h4 className={`font-bold text-xs mb-1 flex items-center gap-2 ${textMain}`}>
                  <span className={`w-4 h-4 flex items-center justify-center ${accentColor}`}><i className="ri-global-line text-xs"></i></span>
                  사이트 URL 직접 추가
                </h4>
                <p className={`${textSub} text-xs mb-3`}>추가한 사이트는 헤르메스가 키워드로 검색합니다(미등록 매체).</p>
                {customSources.length > 0 && (
                  <div className={`flex flex-wrap gap-2 mb-3 p-3 rounded-xl border ${divider}`}>
                    {customSources.map(s => (
                      <span key={s.url} className={`flex items-center gap-1.5 border text-xs px-3 py-1.5 rounded-full ${tagSelected}`}>
                        <i className="ri-links-line text-xs"></i>
                        {sourceDomain(s.url)}{s.name ? ` (${s.name})` : ''}
                        <button type="button" onClick={() => removeCustomSource(s.url)} className="w-3.5 h-3.5 flex items-center justify-center hover:opacity-70 cursor-pointer transition-colors"><i className="ri-close-line text-xs"></i></button>
                      </span>
                    ))}
                  </div>
                )}
                <div className="flex flex-col sm:flex-row gap-2">
                  <input type="text" placeholder="https://example.com/news" value={customSourceUrl}
                    onChange={e => { setCustomSourceUrl(e.target.value); setCustomSourceError(null); }}
                    onKeyDown={onEnterCommit(addCustomSource)}
                    className={`flex-1 rounded-xl px-4 py-2.5 text-sm focus:outline-none transition-colors ${inputBg} ${inputFocus} ${inputText}`} />
                  <input type="text" placeholder="이름(선택)" value={customSourceName}
                    onChange={e => setCustomSourceName(e.target.value)}
                    onKeyDown={onEnterCommit(addCustomSource)}
                    className={`sm:w-40 rounded-xl px-4 py-2.5 text-sm focus:outline-none transition-colors ${inputBg} ${inputFocus} ${inputText}`} />
                  <button type="button" onClick={addCustomSource}
                    className={`flex items-center justify-center gap-2 text-sm font-medium px-4 py-2.5 rounded-xl cursor-pointer whitespace-nowrap transition-colors border ${accentBg} ${accentBorder} ${accentColor} hover:opacity-80`}>
                    <span className="w-4 h-4 flex items-center justify-center"><i className="ri-add-line text-sm"></i></span>추가
                  </button>
                </div>
                {customSourceError && (
                  <p className="text-red-500 text-xs mt-2 flex items-center gap-1"><i className="ri-error-warning-line text-xs"></i>{customSourceError}</p>
                )}
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
                <p className={`${textSub} text-xs`}>{schedule === 'Daily' ? `매일 ${scheduleTime}에 헤르메스가 이 스콥으로 메일을 작성·발송합니다` : `매주 ${weekDay} ${scheduleTime}에 헤르메스가 이 스콥으로 메일을 작성·발송합니다`}</p>
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
                  onKeyDown={onEnterCommit(addEmail)}
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
                <span className="flex items-center gap-1.5"><span className={`w-3.5 h-3.5 flex items-center justify-center ${accentColor}`}><i className="ri-crosshair-2-line text-xs"></i></span>스콥 용어 {scopeTermCount}개</span>
                <span className="flex items-center gap-1.5"><span className={`w-3.5 h-3.5 flex items-center justify-center ${accentColor}`}><i className="ri-newspaper-line text-xs"></i></span>미디어 {selectedMedia.length}개</span>
                <span className="flex items-center gap-1.5"><span className={`w-3.5 h-3.5 flex items-center justify-center ${accentColor}`}><i className="ri-mail-line text-xs"></i></span>수신자 {emailList.length}명</span>
                <span className="flex items-center gap-1.5"><span className={`w-3.5 h-3.5 flex items-center justify-center ${accentColor}`}><i className="ri-time-line text-xs"></i></span>{schedule} {scheduleTime}</span>
              </div>
              <div className="flex items-center gap-2">
                {editingId != null && (
                  <button type="button" onClick={cancelEdit}
                    className={`text-sm font-medium px-4 py-2.5 rounded-xl cursor-pointer whitespace-nowrap transition-colors border ${isDark ? 'border-[#2A3545] text-[#8B9BB4] hover:text-white' : 'border-gray-300 text-gray-500 hover:text-gray-900'}`}>
                    취소
                  </button>
                )}
                <button type="submit" disabled={submitting || scopeTermCount === 0 || selectedMedia.length === 0 || emailList.length === 0}
                  className="flex items-center gap-2 bg-teal-600 text-white text-sm font-bold px-6 py-2.5 rounded-xl cursor-pointer whitespace-nowrap hover:bg-teal-700 transition-colors disabled:opacity-40 disabled:cursor-not-allowed">
                  <span className="w-4 h-4 flex items-center justify-center"><i className={submitting ? 'ri-loader-4-line animate-spin text-sm' : 'ri-save-line text-sm'}></i></span>{submitting ? '저장 중…' : editingId != null ? '수정 저장' : '설정 저장'}
                </button>
              </div>
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
                    <button onClick={() => startEdit(setting)}
                      className={`flex items-center gap-1 text-xs px-3 py-1 rounded-full cursor-pointer whitespace-nowrap transition-all border ${isDark ? 'border-[#2A3545] text-[#8B9BB4] hover:text-white hover:border-[#00E5CC]/40' : 'border-gray-300 text-gray-600 hover:bg-gray-50'}`}>
                      <i className="ri-edit-line text-xs"></i>수정
                    </button>
                    <button onClick={() => handleTestSend(setting.id, setting.name)} disabled={testingId === setting.id}
                      className={`text-xs px-3 py-1 rounded-full cursor-pointer whitespace-nowrap transition-all border disabled:opacity-50 ${isDark ? 'border-[#00E5CC]/30 text-[#00E5CC] hover:bg-[#00E5CC]/10' : 'border-teal-300 text-teal-600 hover:bg-teal-50'}`}>
                      {testingId === setting.id ? '불러오는 중…' : '최근 발송 보기'}
                    </button>
                    <button onClick={() => handleRequestTestMail(setting.id)} disabled={testRequestId === setting.id}
                      className={`flex items-center gap-1 text-xs px-3 py-1 rounded-full cursor-pointer whitespace-nowrap transition-all border disabled:opacity-50 ${isDark ? 'border-[#F59E0B]/30 text-[#F59E0B] hover:bg-[#F59E0B]/10' : 'border-amber-300 text-amber-600 hover:bg-amber-50'}`}>
                      <i className="ri-mail-send-line text-xs"></i>
                      {testRequestId === setting.id ? '요청 중…' : '테스트 메일 요청'}
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
                {testRequestNote && testRequestNote.id === setting.id && (
                  <div className={`mb-3 flex items-center gap-2 rounded-lg px-3 py-2 text-xs ${testRequestNote.ok ? (isDark ? 'bg-[#00E5CC]/10 text-[#00E5CC]' : 'bg-teal-50 text-teal-700') : (isDark ? 'bg-red-500/10 text-red-400' : 'bg-red-50 text-red-600')}`}>
                    <i className={testRequestNote.ok ? 'ri-checkbox-circle-line text-xs' : 'ri-error-warning-line text-xs'}></i>
                    {testRequestNote.message}
                  </div>
                )}
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <p className={`${textMuted} text-xs mb-2`}>자유 키워드</p>
                    <div className="flex flex-wrap gap-1.5">
                      {setting.keywords.map(kw => <span key={kw} className={`text-xs px-2.5 py-1 rounded-full border ${tagDefault}`}>{kw}</span>)}
                      {setting.keywords.length === 0 && <span className={`text-xs ${textMuted}`}>—</span>}
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
                {((setting.brands?.length ?? 0) > 0 || (setting.companies?.length ?? 0) > 0 || (setting.policy_topics?.length ?? 0) > 0 || (setting.disease_areas?.length ?? 0) > 0) && (
                  <div className={`mt-3 pt-3 border-t flex flex-wrap gap-1.5 ${divider}`}>
                    {setting.brands?.map(b => (
                      <span key={`brand-${b}`} className={`flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full border ${tagDefault}`}>
                        <i className="ri-capsule-line text-[10px]"></i>{b}
                      </span>
                    ))}
                    {setting.companies?.map(c => (
                      <span key={`company-${c}`} className={`flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full border ${tagDefault}`}>
                        <i className="ri-building-2-line text-[10px]"></i>{c}
                      </span>
                    ))}
                    {setting.policy_topics?.map(p => (
                      <span key={`policy-${p}`} className={`flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full border ${tagDefault}`}>
                        <i className="ri-government-line text-[10px]"></i>{p}
                      </span>
                    ))}
                    {setting.disease_areas?.map(d => (
                      <span key={`disease-${d}`} className={`flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full border ${tagDefault}`}>
                        <i className="ri-heart-pulse-line text-[10px]"></i>{d}
                      </span>
                    ))}
                  </div>
                )}
                {(setting.custom_sources?.length ?? 0) > 0 && (
                  <div className={`mt-3 pt-3 border-t flex flex-wrap gap-1.5 ${divider}`}>
                    {setting.custom_sources?.map(s => (
                      <span key={`src-${s.url}`} className={`flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full border ${tagDefault}`}>
                        <i className="ri-global-line text-[10px]"></i>{sourceDomain(s.url)}{s.name ? ` (${s.name})` : ''}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {previewModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4" onClick={() => setPreviewModal(null)}>
          <div className={`absolute inset-0 ${isDark ? 'bg-black/70' : 'bg-black/40'} backdrop-blur-sm`} />
          <div
            onClick={e => e.stopPropagation()}
            className={`relative w-full max-w-3xl max-h-[85vh] overflow-y-auto rounded-2xl border shadow-2xl ${cardBg} ${cardBorder}`}
          >
            <button
              onClick={() => setPreviewModal(null)}
              className={`absolute top-4 right-4 w-9 h-9 flex items-center justify-center rounded-full cursor-pointer transition-colors z-10 ${isDark ? 'bg-[#0D1117] text-[#8B9BB4] hover:text-white hover:bg-[#1E2530]' : 'bg-gray-100 text-gray-400 hover:text-gray-700 hover:bg-gray-200'}`}
            >
              <i className="ri-close-line text-lg"></i>
            </button>
            <div className="p-6">
              <div className="flex items-center gap-2 mb-1">
                <span className={`w-5 h-5 flex items-center justify-center ${accentColor}`}><i className="ri-mail-open-line text-sm"></i></span>
                <h3 className={`font-bold text-sm ${textMain}`}>최근 발송 브리프 — {previewModal.subscriptionName}</h3>
              </div>
              <p className={`${textSub} text-xs mb-4`}>
                아래는 이 스콥으로 헤르메스가 실제 작성한 최신 브리프입니다. 대쉬보드는 메일을 직접 발송하지 않습니다.
              </p>
              <div className={`rounded-xl border px-4 py-3 mb-4 ${previewBg}`}>
                <p className={`${textMuted} text-[10px] mb-1`}>제목</p>
                <p className={`${textMain} text-sm font-semibold`}>{previewModal.subject}</p>
              </div>
              <div className={`rounded-xl border overflow-hidden ${cardBorder}`}>
                <iframe
                  title="메일 미리보기"
                  srcDoc={previewModal.html}
                  sandbox=""
                  className="w-full bg-white"
                  style={{ height: '480px' }}
                />
              </div>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}