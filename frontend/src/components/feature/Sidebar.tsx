import { useState, useRef, useEffect, useMemo } from 'react';
import { NavLink, useNavigate, useLocation } from 'react-router-dom';
import {
  listUsers,
  addUser,
  removeUser,
  updateMyPassword,
  logout,
  isAdmin,
  getCurrentUser,
  ADMIN_EMAIL,
  AppUser,
} from '@/utils/authUsers';
import { getTabVisibility, setTabVisibility, isTabVisible } from '@/utils/tabVisibility';
import { fetchOutboxCount } from '@/api/serviceRequests';
import { fetchPendingProposalCount, SV_PROPOSALS_CHANGED_EVENT } from '@/api/stakeholder';

export interface NavItem {
  path: string;
  label: string;
  icon: string;
  exact?: boolean;
  group?: string;
}

// 업무 흐름 기준 그룹 — 가격 확인 → 급여·정책 판단 → 시장 → 지원
export const allNavItems: NavItem[] = [
  { path: '/', label: '대시보드', icon: 'ri-dashboard-line', exact: true, group: '개요' },
  { path: '/domestic-pricing', label: '국내 약가', icon: 'ri-price-tag-3-line', group: '가격' },
  { path: '/international-pricing', label: '해외 약가', icon: 'ri-global-line', group: '가격' },
  { path: '/regimen-cost', label: '요법 비용', icon: 'ri-bar-chart-box-line', group: '가격' },
  { path: '/reimbursement-status', label: '급여 현황', icon: 'ri-article-line', group: '급여·정책' },
  { path: '/policy-intelligence', label: '정책 인텔리전스', icon: 'ri-government-line', group: '급여·정책' },
  { path: '/stakeholder-signals', label: '정책 발언', icon: 'ri-user-voice-line', group: '급여·정책' },
  { path: '/analog-search', label: '등재 아날로그', icon: 'ri-search-eye-line', group: '급여·정책' },
  { path: '/market-share', label: '시장점유율', icon: 'ri-pie-chart-2-line', group: '시장' },
  { path: '/competitor-trends', label: '경쟁사 동향', icon: 'ri-radar-line', group: '시장' },
  { path: '/daily-mailing', label: '데일리 메일링', icon: 'ri-mail-line', group: '시장' },
  { path: '/my-requests', label: '내 개선 요청', icon: 'ri-feedback-line', group: '지원' },
];

// admin 전용 — tabVisibility 미적용 (관리 기능은 항상 노출)
export const adminNavItems: NavItem[] = [
  { path: '/admin/agent-tasks', label: '운영 원장', icon: 'ri-pulse-line', group: '운영' },
  { path: '/admin/service-requests', label: '서비스 보완 요청', icon: 'ri-customer-service-2-line', group: '운영' },
  { path: '/admin/daily-mailing-kanban', label: '메일링 칸반', icon: 'ri-kanban-view', group: '운영' },
  { path: '/admin/stakeholder-ops', label: '모니터링 운영 현황', icon: 'ri-heart-pulse-line', group: '운영' },
  { path: '/admin/stakeholder-audit', label: '모니터링 감사', icon: 'ri-shield-check-line', group: '운영' },
  { path: '/admin/stakeholder-people', label: '모니터링 인물', icon: 'ri-contacts-line', group: '운영' },
  { path: '/admin/reimbursement', label: '급여 관리', icon: 'ri-health-book-line', group: '데이터 관리' },
  { path: '/admin/reimbursement-pipeline', label: '심의 파이프라인', icon: 'ri-route-line', group: '데이터 관리' },
  { path: '/admin/market-share', label: '시장점유율 업로드', icon: 'ri-upload-cloud-2-line', group: '데이터 관리' },
  { path: '/admin/msd-pipeline', label: 'MSD 파이프라인', icon: 'ri-git-branch-line', group: '데이터 관리' },
  { path: '/admin/competitor-trends', label: 'MNC 동향 관리', icon: 'ri-building-2-line', group: '데이터 관리' },
  { path: '/admin/home-brands', label: '홈 브랜드 관리', icon: 'ri-capsule-line', group: '데이터 관리' },
];

function groupItems(items: NavItem[]): [string, NavItem[]][] {
  const out: [string, NavItem[]][] = [];
  for (const it of items) {
    const g = it.group || '';
    const last = out[out.length - 1];
    if (last && last[0] === g) last[1].push(it);
    else out.push([g, [it]]);
  }
  return out;
}

type SettingsTab = 'account' | 'users' | 'tabs';

export default function Sidebar() {
  const navigate = useNavigate();
  const location = useLocation();
  const currentUser = getCurrentUser();
  const adminMode = isAdmin(currentUser);

  const [showSettings, setShowSettings] = useState(false);
  const [activeTab, setActiveTab] = useState<SettingsTab>('account');
  const [tabVisibility, setTabVisibilityState] = useState(getTabVisibility);

  // Account tab state (email is identity — backend can't rename, so read-only)
  const adminEmail = currentUser || ADMIN_EMAIL;
  const [adminPw, setAdminPw] = useState('');
  const [adminPwConfirm, setAdminPwConfirm] = useState('');
  const [showAdminPw, setShowAdminPw] = useState(false);
  const [showAdminPwConfirm, setShowAdminPwConfirm] = useState(false);
  const [accountMsg, setAccountMsg] = useState('');
  const [accountError, setAccountError] = useState('');
  const [savingAccount, setSavingAccount] = useState(false);

  // Users tab state
  const [users, setUsers] = useState<AppUser[]>([]);
  const [loadingUsers, setLoadingUsers] = useState(false);
  const [newEmail, setNewEmail] = useState('');
  const [newPw, setNewPw] = useState('');
  const [showNewPw, setShowNewPw] = useState(false);
  const [userMsg, setUserMsg] = useState('');
  const [userError, setUserError] = useState('');
  const [addingUser, setAddingUser] = useState(false);

  const popupRef = useRef<HTMLDivElement>(null);

  // 서비스 보완 요청 outbox(sent) 대기 건수 배지 — admin 전용.
  // 비관리자는 절대 fetch 하지 않고, 실패 시 0(배지 없음)으로 조용히 처리.
  const [outboxCount, setOutboxCount] = useState(0);
  useEffect(() => {
    if (!adminMode) return;
    let cancelled = false;
    const refresh = () => {
      void fetchOutboxCount().then(n => {
        if (!cancelled) setOutboxCount(n);
      });
    };
    refresh();
    const timer = window.setInterval(refresh, 60_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
    // location.pathname: 라우트 이동 시 재조회 (sent 처리 직후 배지 최신화)
  }, [adminMode, location.pathname]);

  // 모니터링 감사 미검토 제안(needs_review + needs_evidence) 배지 — admin 전용, 같은 폴링 패턴.
  // 감사 페이지가 승인·반려 직후 SV_PROPOSALS_CHANGED_EVENT 를 쏘면 즉시 재조회. 실패 시 0(배지 없음).
  const [svPendingCount, setSvPendingCount] = useState(0);
  useEffect(() => {
    if (!adminMode) return;
    let cancelled = false;
    const refresh = () => {
      void fetchPendingProposalCount().then(n => {
        if (!cancelled) setSvPendingCount(n);
      });
    };
    refresh();
    const timer = window.setInterval(refresh, 60_000);
    window.addEventListener(SV_PROPOSALS_CHANGED_EVENT, refresh);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      window.removeEventListener(SV_PROPOSALS_CHANGED_EVENT, refresh);
    };
  }, [adminMode, location.pathname]);

  // 현재 경로가 숨겨진 탭이면 홈으로 리다이렉트
  useEffect(() => {
    if (!isTabVisible(location.pathname)) {
      navigate('/', { replace: true });
    }
  }, [location.pathname, navigate]);

  const visibleNavItems = useMemo(
    () => allNavItems.filter(item => isTabVisible(item.path)),
    [tabVisibility],
  );

  const reloadUsers = async () => {
    setLoadingUsers(true);
    setUserError('');
    try {
      const all = await listUsers();
      setUsers(all.filter(u => u.role !== 'admin'));
    } catch (e) {
      setUserError(e instanceof Error ? e.message : '유저 목록을 불러오지 못했습니다.');
    } finally {
      setLoadingUsers(false);
    }
  };

  useEffect(() => {
    if (showSettings) {
      setTabVisibilityState(getTabVisibility());
      void reloadUsers();
    }
  }, [showSettings]);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (popupRef.current && !popupRef.current.contains(e.target as Node)) {
        closeSettings();
      }
    };
    if (showSettings) document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [showSettings]);

  const closeSettings = () => {
    setShowSettings(false);
    setAccountMsg('');
    setAccountError('');
    setUserMsg('');
    setUserError('');
    setAdminPw('');
    setAdminPwConfirm('');
    setNewEmail('');
    setNewPw('');
  };

  const handleSaveAccount = async () => {
    setAccountMsg('');
    setAccountError('');
    if (!adminPw) {
      setAccountError('새 비밀번호를 입력해주세요.');
      return;
    }
    if (adminPw.length < 6) {
      setAccountError('비밀번호는 6자 이상이어야 합니다.');
      return;
    }
    if (adminPw !== adminPwConfirm) {
      setAccountError('비밀번호가 일치하지 않습니다.');
      return;
    }
    setSavingAccount(true);
    const result = await updateMyPassword(adminPw);
    setSavingAccount(false);
    if (!result.ok) {
      setAccountError(result.error || '저장 실패');
      return;
    }
    setAccountMsg('비밀번호가 변경되었습니다.');
    setAdminPw('');
    setAdminPwConfirm('');
    setTimeout(() => setAccountMsg(''), 2500);
  };

  const handleAddUser = async () => {
    setUserMsg('');
    setUserError('');
    if (!newEmail.trim() || !newEmail.includes('@')) {
      setUserError('올바른 이메일 주소를 입력해주세요.');
      return;
    }
    if (!newPw || newPw.length < 4) {
      setUserError('비밀번호는 4자 이상이어야 합니다.');
      return;
    }
    setAddingUser(true);
    const result = await addUser(newEmail.trim(), newPw);
    setAddingUser(false);
    if (!result.ok) {
      setUserError(result.error || '추가 실패');
      return;
    }
    await reloadUsers();
    const added = newEmail.trim();
    setNewEmail('');
    setNewPw('');
    setUserMsg(`${added} 계정이 추가되었습니다.`);
    setTimeout(() => setUserMsg(''), 2500);
  };

  const handleRemoveUser = async (email: string) => {
    setUserError('');
    const result = await removeUser(email);
    if (!result.ok) {
      setUserError(result.error || '삭제 실패');
      return;
    }
    await reloadUsers();
  };

  const handleToggleTab = (path: string) => {
    const newVisible = !tabVisibility[path];
    setTabVisibility(path, newVisible);
    setTabVisibilityState(getTabVisibility());
  };

  const handleLogout = () => {
    logout();
    closeSettings();
    navigate('/login');
  };

  const displayEmail = currentUser || ADMIN_EMAIL;
  const initials = displayEmail.slice(0, 2).toUpperCase();

  return (
    <aside className="ui-sidebar fixed left-0 top-0 h-screen w-64 flex flex-col z-50">
      {/* Brand */}
      <div className="flex items-center gap-2.5 px-5 pt-6 pb-3">
        <span className="w-8 h-8 rounded-[9px] flex items-center justify-center" style={{ background: 'linear-gradient(180deg,#2b8cff,#0060df)' }}>
          <i className="ri-capsule-fill text-white text-base"></i>
        </span>
        <div className="leading-tight">
          <p className="text-[15px] font-semibold tracking-tight" style={{ color: 'var(--label)' }}>Market Access</p>
          <p className="text-[11px]" style={{ color: 'var(--label-3)' }}>MSD Korea Intelligence Hub</p>
        </div>
      </div>

      {/* Navigation — 업무 흐름 그룹 */}
      <nav className="flex-1 px-3 pb-4 overflow-y-auto" aria-label="주요 메뉴">
        {groupItems(visibleNavItems).map(([group, items]) => (
          <div key={group}>
            {group && <p className="ui-nav-section">{group}</p>}
            <ul className="space-y-0.5">
              {items.map(item => (
                <li key={item.path}>
                  <NavLink to={item.path} end={item.exact}
                    className={({ isActive }) => `ui-nav-item${isActive ? ' is-active' : ''}`}>
                    <i className={item.icon} aria-hidden="true"></i>
                    <span className="truncate">{item.label}</span>
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        ))}

        {adminMode && (
          <div className="mt-3 pt-2" style={{ borderTop: '1px solid var(--separator)' }}>
            {groupItems(adminNavItems).map(([group, items]) => (
              <div key={group}>
                <p className="ui-nav-section">관리 · {group}</p>
                <ul className="space-y-0.5">
                  {items.map(item => (
                    <li key={item.path}>
                      <NavLink to={item.path}
                        className={({ isActive }) => `ui-nav-item${isActive ? ' is-active' : ''}`}>
                        <i className={item.icon} aria-hidden="true"></i>
                        <span className="truncate">{item.label}</span>
                        {item.path === '/admin/service-requests' && outboxCount > 0 && (
                          <span className="ui-nav-badge is-alert" title={`미처리 요청(outbox) ${outboxCount}건`}>
                            {outboxCount > 99 ? '99+' : outboxCount}
                          </span>
                        )}
                        {item.path === '/admin/stakeholder-audit' && svPendingCount > 0 && (
                          <span className="ui-nav-badge is-alert" title={`미검토 감사 제안 ${svPendingCount}건`}>
                            {svPendingCount > 99 ? '99+' : svPendingCount}
                          </span>
                        )}
                      </NavLink>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
      </nav>

      {/* User Profile + Settings (admin only) */}
      <div className="px-4 py-3 relative" style={{ borderTop: '1px solid var(--separator)' }} ref={popupRef}>
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-full flex items-center justify-center text-white text-xs font-semibold flex-shrink-0" style={{ background: 'linear-gradient(180deg,#8e8e93,#636366)' }}>
            {initials}
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-white text-xs font-semibold truncate">
              {adminMode ? 'Admin' : 'User'}
            </p>
            <p className="text-[#8B9BB4] text-xs truncate">{displayEmail}</p>
          </div>
          {/* 설정 아이콘 — admin 전용 */}
          {adminMode && (
            <button
              onClick={() => { setShowSettings(v => !v); }}
              className="w-6 h-6 flex items-center justify-center cursor-pointer rounded-md hover:bg-black/5 transition-colors"
              title="관리자 설정"
            >
              <i className={`ri-settings-3-line text-sm transition-colors ${showSettings ? 'text-[#00E5CC]' : 'text-[#8B9BB4] hover:text-white'}`}></i>
            </button>
          )}
          {/* 로그아웃 — 모든 유저 */}
          <button
            onClick={handleLogout}
            className="w-6 h-6 flex items-center justify-center cursor-pointer rounded-md hover:bg-black/5 transition-colors"
            title="로그아웃"
          >
            <i className="ri-logout-box-r-line text-sm text-[#8B9BB4] hover:text-red-400 transition-colors"></i>
          </button>
        </div>

        {/* Admin Settings Popup */}
        {showSettings && adminMode && (
          <div className="absolute bottom-full left-3 right-3 mb-2 bg-[#161B27] border border-[#1E2530] rounded-2xl shadow-2xl overflow-hidden z-50 max-h-[75vh]">
            {/* Popup Header */}
            <div className="flex items-center gap-2 px-4 py-3 border-b border-[#1E2530]">
              <span className="w-5 h-5 flex items-center justify-center">
                <i className="ri-shield-user-line text-[#00E5CC] text-sm"></i>
              </span>
              <p className="text-white text-sm font-bold">관리자 설정</p>
              <button
                onClick={closeSettings}
                className="ml-auto w-5 h-5 flex items-center justify-center text-[#4A5568] hover:text-white cursor-pointer transition-colors"
              >
                <i className="ri-close-line text-sm"></i>
              </button>
            </div>

            {/* Tabs */}
            <div className="flex border-b border-[#1E2530] overflow-x-auto">
              <button
                onClick={() => setActiveTab('account')}
                className={`flex-1 py-2.5 text-xs font-semibold transition-colors cursor-pointer whitespace-nowrap ${
                  activeTab === 'account'
                    ? 'text-[#00E5CC] border-b-2 border-[#00E5CC]'
                    : 'text-[#4A5568] hover:text-white'
                }`}
              >
                <i className="ri-user-settings-line mr-1"></i>
                내 계정
              </button>
              <button
                onClick={() => setActiveTab('users')}
                className={`flex-1 py-2.5 text-xs font-semibold transition-colors cursor-pointer whitespace-nowrap ${
                  activeTab === 'users'
                    ? 'text-[#00E5CC] border-b-2 border-[#00E5CC]'
                    : 'text-[#4A5568] hover:text-white'
                }`}
              >
                <i className="ri-team-line mr-1"></i>
                접속 관리
              </button>
              <button
                onClick={() => setActiveTab('tabs')}
                className={`flex-1 py-2.5 text-xs font-semibold transition-colors cursor-pointer whitespace-nowrap ${
                  activeTab === 'tabs'
                    ? 'text-[#00E5CC] border-b-2 border-[#00E5CC]'
                    : 'text-[#4A5568] hover:text-white'
                }`}
              >
                <i className="ri-layout-4-line mr-1"></i>
                탭 관리
              </button>
            </div>

            {/* Tab: 내 계정 */}
            {activeTab === 'account' && (
              <div className="px-4 py-4 space-y-3">
                <div>
                  <label className="block text-[#8B9BB4] text-xs font-semibold mb-1.5">
                    <i className="ri-mail-line mr-1"></i>Admin 이메일
                  </label>
                  <input
                    type="email"
                    value={adminEmail}
                    readOnly
                    disabled
                    className="w-full bg-[#0D1117] border border-[#1E2530] rounded-lg px-3 py-2 text-[#8B9BB4] text-xs placeholder-[#4A5568] focus:outline-none cursor-not-allowed opacity-70"
                  />
                  <p className="text-[#4A5568] text-[10px] mt-1">이메일은 계정 식별자로 변경할 수 없습니다.</p>
                </div>
                <div>
                  <label className="block text-[#8B9BB4] text-xs font-semibold mb-1.5">
                    <i className="ri-lock-line mr-1"></i>새 비밀번호
                  </label>
                  <div className="relative">
                    <input
                      type={showAdminPw ? 'text' : 'password'}
                      value={adminPw}
                      onChange={e => setAdminPw(e.target.value)}
                      className="w-full bg-[#0D1117] border border-[#1E2530] rounded-lg px-3 py-2 pr-8 text-white text-xs placeholder-[#4A5568] focus:outline-none focus:border-[#00E5CC]/50 transition-colors"
                      placeholder="변경할 비밀번호 (6자 이상)"
                    />
                    <button type="button" onClick={() => setShowAdminPw(v => !v)} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[#4A5568] hover:text-white cursor-pointer transition-colors">
                      <i className={`${showAdminPw ? 'ri-eye-off-line' : 'ri-eye-line'} text-xs`}></i>
                    </button>
                  </div>
                </div>
                <div>
                  <label className="block text-[#8B9BB4] text-xs font-semibold mb-1.5">
                    <i className="ri-lock-2-line mr-1"></i>비밀번호 확인
                  </label>
                  <div className="relative">
                    <input
                      type={showAdminPwConfirm ? 'text' : 'password'}
                      value={adminPwConfirm}
                      onChange={e => setAdminPwConfirm(e.target.value)}
                      className="w-full bg-[#0D1117] border border-[#1E2530] rounded-lg px-3 py-2 pr-8 text-white text-xs placeholder-[#4A5568] focus:outline-none focus:border-[#00E5CC]/50 transition-colors"
                      placeholder="비밀번호 재입력"
                    />
                    <button type="button" onClick={() => setShowAdminPwConfirm(v => !v)} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[#4A5568] hover:text-white cursor-pointer transition-colors">
                      <i className={`${showAdminPwConfirm ? 'ri-eye-off-line' : 'ri-eye-line'} text-xs`}></i>
                    </button>
                  </div>
                </div>

                {accountError && (
                  <p className="text-red-400 text-xs flex items-center gap-1">
                    <i className="ri-error-warning-line text-xs"></i>{accountError}
                  </p>
                )}
                {accountMsg && (
                  <p className="text-emerald-400 text-xs flex items-center gap-1">
                    <i className="ri-check-line text-xs"></i>{accountMsg}
                  </p>
                )}

                <button
                  onClick={handleSaveAccount}
                  disabled={savingAccount}
                  className="w-full bg-[#00E5CC] text-[#0A0E1A] text-xs font-bold py-2 rounded-lg cursor-pointer hover:bg-[#00C9B1] transition-colors whitespace-nowrap disabled:opacity-60 disabled:cursor-not-allowed"
                >
                  {savingAccount ? '저장 중...' : '비밀번호 변경'}
                </button>
              </div>
            )}

            {/* Tab: 접속 관리 */}
            {activeTab === 'users' && (
              <div className="px-4 py-4 space-y-3">
                {/* 유저 추가 */}
                <div className="bg-[#0D1117] rounded-xl p-3 space-y-2">
                  <p className="text-[#8B9BB4] text-xs font-semibold">
                    <i className="ri-user-add-line mr-1"></i>새 계정 추가
                  </p>
                  <input
                    type="email"
                    value={newEmail}
                    onChange={e => setNewEmail(e.target.value)}
                    className="w-full bg-[#161B27] border border-[#1E2530] rounded-lg px-3 py-2 text-white text-xs placeholder-[#4A5568] focus:outline-none focus:border-[#00E5CC]/50 transition-colors"
                    placeholder="이메일 주소"
                  />
                  <div className="relative">
                    <input
                      type={showNewPw ? 'text' : 'password'}
                      value={newPw}
                      onChange={e => setNewPw(e.target.value)}
                      className="w-full bg-[#161B27] border border-[#1E2530] rounded-lg px-3 py-2 pr-8 text-white text-xs placeholder-[#4A5568] focus:outline-none focus:border-[#00E5CC]/50 transition-colors"
                      placeholder="비밀번호 (4자 이상)"
                    />
                    <button type="button" onClick={() => setShowNewPw(v => !v)} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[#4A5568] hover:text-white cursor-pointer transition-colors">
                      <i className={`${showNewPw ? 'ri-eye-off-line' : 'ri-eye-line'} text-xs`}></i>
                    </button>
                  </div>

                  {userError && (
                    <p className="text-red-400 text-xs flex items-center gap-1">
                      <i className="ri-error-warning-line text-xs"></i>{userError}
                    </p>
                  )}
                  {userMsg && (
                    <p className="text-emerald-400 text-xs flex items-center gap-1">
                      <i className="ri-check-line text-xs"></i>{userMsg}
                    </p>
                  )}

                  <button
                    onClick={handleAddUser}
                    disabled={addingUser}
                    className="w-full bg-[#00E5CC]/10 border border-[#00E5CC]/30 text-[#00E5CC] text-xs font-semibold py-2 rounded-lg cursor-pointer hover:bg-[#00E5CC]/20 transition-colors whitespace-nowrap disabled:opacity-60 disabled:cursor-not-allowed"
                  >
                    <i className="ri-add-line mr-1"></i>{addingUser ? '추가 중...' : '계정 추가'}
                  </button>
                </div>

                {/* 유저 목록 */}
                <div>
                  <p className="text-[#4A5568] text-xs font-semibold mb-2">
                    <i className="ri-group-line mr-1"></i>
                    접속 가능 계정 ({users.length}명)
                  </p>
                  {loadingUsers ? (
                    <div className="text-center py-4">
                      <p className="text-[#4A5568] text-xs">불러오는 중...</p>
                    </div>
                  ) : users.length === 0 ? (
                    <div className="text-center py-4">
                      <p className="text-[#4A5568] text-xs">등록된 유저가 없습니다.</p>
                      <p className="text-[#2A3545] text-xs mt-1">위에서 계정을 추가해주세요.</p>
                    </div>
                  ) : (
                    <ul className="space-y-1.5 max-h-40 overflow-y-auto">
                      {users.map(u => (
                        <li key={u.email} className="flex items-center gap-2 bg-[#0D1117] rounded-lg px-3 py-2">
                          <span className="w-5 h-5 rounded-full bg-[#1E2530] flex items-center justify-center flex-shrink-0">
                            <i className="ri-user-line text-[10px] text-[#8B9BB4]"></i>
                          </span>
                          <div className="flex-1 min-w-0">
                            <p className="text-white text-xs truncate">{u.email}</p>
                            <p className="text-[#4A5568] text-[10px]">추가일: {u.createdAt}</p>
                          </div>
                          <button
                            onClick={() => handleRemoveUser(u.email)}
                            className="w-5 h-5 flex items-center justify-center text-[#4A5568] hover:text-red-400 cursor-pointer transition-colors flex-shrink-0"
                            title="삭제"
                          >
                            <i className="ri-delete-bin-line text-xs"></i>
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            )}

            {/* Tab: 탭 관리 */}
            {activeTab === 'tabs' && (
              <div className="px-4 py-4 space-y-3">
                <p className="text-[#8B9BB4] text-xs">
                  <i className="ri-layout-4-line mr-1"></i>
                  각 분석 탭의 사이드바 노출 여부를 설정합니다. 비활성화된 탭은 메뉴에서 숨겨집니다.
                </p>

                <div className="space-y-1">
                  {allNavItems.map((item) => {
                    const visible = tabVisibility[item.path] !== false;
                    return (
                      <div
                        key={item.path}
                        className="flex items-center gap-3 bg-[#0D1117] rounded-lg px-3 py-2.5"
                      >
                        <span className="w-5 h-5 flex items-center justify-center flex-shrink-0">
                          <i className={`${item.icon} text-sm ${visible ? 'text-[#00E5CC]' : 'text-[#4A5568]'}`}></i>
                        </span>
                        <div className="flex-1 min-w-0">
                          <p className={`text-xs font-semibold truncate ${visible ? 'text-white' : 'text-[#4A5568]'}`}>
                            {item.label}
                          </p>
                          <p className="text-[#2A3545] text-[10px] truncate">{item.path}</p>
                        </div>
                        {/* Toggle Switch */}
                        <button
                          onClick={() => handleToggleTab(item.path)}
                          className={`relative w-10 h-5 rounded-full transition-colors duration-200 flex-shrink-0 cursor-pointer ${
                            visible ? 'bg-[#00E5CC]' : 'bg-[#2A3545]'
                          }`}
                        >
                          <span
                            className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow-sm transition-all duration-200 ${
                              visible ? 'left-5' : 'left-0.5'
                            }`}
                          ></span>
                        </button>
                      </div>
                    );
                  })}
                </div>

                <div className="bg-[#0D1117] rounded-lg p-3 flex items-start gap-2">
                  <span className="w-4 h-4 flex items-center justify-center text-[#F59E0B] flex-shrink-0 mt-0.5">
                    <i className="ri-information-line text-xs"></i>
                  </span>
                  <p className="text-[#8B9BB4] text-xs leading-relaxed">
                    변경 사항은 즉시 반영되며, 모든 사용자에게 동일하게 적용됩니다. 비활성화된 탭에 직접 접근하면 홈으로 리다이렉트됩니다.
                  </p>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </aside>
  );
}
