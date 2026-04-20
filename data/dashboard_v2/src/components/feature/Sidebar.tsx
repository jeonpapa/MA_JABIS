import { useState, useRef, useEffect } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import {
  listUsers,
  addUser,
  removeUser,
  updateMyPassword,
  isAdmin,
  getCurrentUser,
  logout,
  ADMIN_EMAIL,
  AppUser,
} from '@/utils/authUsers';

const navItems = [
  { path: '/', label: 'Dashboard Overview', icon: 'ri-dashboard-3-line', exact: true },
  { path: '/domestic-pricing', label: 'Domestic Pricing', icon: 'ri-price-tag-3-line' },
  { path: '/international-pricing', label: 'International Pricing', icon: 'ri-global-line' },
  { path: '/market-share', label: 'Korean Market', icon: 'ri-pie-chart-2-line' },
  { path: '/competitor-trends', label: 'Competitor Trends', icon: 'ri-bar-chart-grouped-line' },
  { path: '/workbench', label: 'Negotiation Workbench', icon: 'ri-scales-3-line' },
  { path: '/daily-mailing', label: 'Daily Mailing', icon: 'ri-mail-settings-line' },
];

type SettingsTab = 'account' | 'users';

export default function Sidebar() {
  const navigate = useNavigate();
  const currentUser = getCurrentUser();
  const adminMode = isAdmin(currentUser);

  const [showSettings, setShowSettings] = useState(false);
  const [activeTab, setActiveTab] = useState<SettingsTab>('account');

  // Account tab state — admin 이메일은 서버가 DB 키이므로 변경 불가. 비밀번호만 변경.
  const [adminPw, setAdminPw] = useState('');
  const [adminPwConfirm, setAdminPwConfirm] = useState('');
  const [showAdminPw, setShowAdminPw] = useState(false);
  const [showAdminPwConfirm, setShowAdminPwConfirm] = useState(false);
  const [accountMsg, setAccountMsg] = useState('');
  const [accountError, setAccountError] = useState('');
  const [accountSaving, setAccountSaving] = useState(false);

  // Users tab state
  const [users, setUsers] = useState<AppUser[]>([]);
  const [usersLoading, setUsersLoading] = useState(false);
  const [newEmail, setNewEmail] = useState('');
  const [newPw, setNewPw] = useState('');
  const [showNewPw, setShowNewPw] = useState(false);
  const [userMsg, setUserMsg] = useState('');
  const [userError, setUserError] = useState('');
  const [addBusy, setAddBusy] = useState(false);

  const popupRef = useRef<HTMLDivElement>(null);

  const refreshUsers = async () => {
    setUsersLoading(true);
    try {
      const all = await listUsers();
      setUsers(all.filter(u => u.role !== 'admin'));
    } catch (e: any) {
      setUserError(e?.message || '유저 목록을 불러오지 못했습니다.');
    } finally {
      setUsersLoading(false);
    }
  };

  useEffect(() => {
    if (showSettings && activeTab === 'users') {
      refreshUsers();
    }
  }, [showSettings, activeTab]);

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
    if (!adminPw || adminPw.length < 4) {
      setAccountError('비밀번호는 4자 이상이어야 합니다.');
      return;
    }
    if (adminPw !== adminPwConfirm) {
      setAccountError('비밀번호가 일치하지 않습니다.');
      return;
    }
    setAccountSaving(true);
    const res = await updateMyPassword(adminPw);
    setAccountSaving(false);
    if (!res.ok) {
      setAccountError(res.error || '변경에 실패했습니다.');
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
    setAddBusy(true);
    const result = await addUser(newEmail.trim(), newPw);
    setAddBusy(false);
    if (!result.ok) {
      setUserError(result.error || '추가 실패');
      return;
    }
    await refreshUsers();
    const added = newEmail.trim();
    setNewEmail('');
    setNewPw('');
    setUserMsg(`${added} 계정이 추가되었습니다.`);
    setTimeout(() => setUserMsg(''), 2500);
  };

  const handleRemoveUser = async (email: string) => {
    const res = await removeUser(email);
    if (!res.ok) {
      setUserError(res.error || '삭제 실패');
      return;
    }
    await refreshUsers();
  };

  const handleLogout = () => {
    logout();
    closeSettings();
    navigate('/login');
  };

  const displayEmail = currentUser || ADMIN_EMAIL;
  const initials = displayEmail.slice(0, 2).toUpperCase();

  return (
    <aside className="fixed left-0 top-0 h-screen w-60 bg-[#0A0E1A] flex flex-col z-50 border-r border-[#1E2530]">
      {/* Logo */}
      <div className="flex items-center gap-3 px-5 py-5 border-b border-[#1E2530]">
        <img
          src="https://public.readdy.ai/ai/img_res/be30245e-c610-43e9-9d9b-6faaf65094e2.png"
          alt="Logo"
          className="w-8 h-8 object-contain"
        />
        <div>
          <p className="text-white font-bold text-sm leading-tight">Market Intel</p>
          <p className="text-[#8B9BB4] text-xs">Market Access Hub</p>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-3 py-4 overflow-y-auto">
        <p className="text-[#4A5568] text-xs font-semibold uppercase tracking-wider px-3 mb-3">Analytics</p>
        <ul className="space-y-1">
          {navItems.map((item) => (
            <li key={item.path}>
              <NavLink
                to={item.path}
                end={item.exact}
                className={({ isActive }) =>
                  `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all duration-200 cursor-pointer whitespace-nowrap relative ${
                    isActive
                      ? 'text-[#00E5CC] bg-[#00E5CC]/10 before:absolute before:left-0 before:top-1/2 before:-translate-y-1/2 before:w-1 before:h-6 before:bg-[#00E5CC] before:rounded-r-full'
                      : 'text-[#8B9BB4] hover:text-white hover:bg-white/5'
                  }`
                }
              >
                <span className="w-5 h-5 flex items-center justify-center">
                  <i className={`${item.icon} text-base`}></i>
                </span>
                {item.label}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>

      {/* User Profile + Settings (admin only) */}
      <div className="px-4 py-4 border-t border-[#1E2530] relative" ref={popupRef}>
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-full bg-gradient-to-br from-[#00E5CC] to-[#7C3AED] flex items-center justify-center text-white text-xs font-bold flex-shrink-0">
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
              className="w-6 h-6 flex items-center justify-center cursor-pointer rounded-md hover:bg-white/10 transition-colors"
              title="관리자 설정"
            >
              <i className={`ri-settings-3-line text-sm transition-colors ${showSettings ? 'text-[#00E5CC]' : 'text-[#8B9BB4] hover:text-white'}`}></i>
            </button>
          )}
          {/* 로그아웃 — 모든 유저 */}
          <button
            onClick={handleLogout}
            className="w-6 h-6 flex items-center justify-center cursor-pointer rounded-md hover:bg-white/10 transition-colors"
            title="로그아웃"
          >
            <i className="ri-logout-box-r-line text-sm text-[#8B9BB4] hover:text-red-400 transition-colors"></i>
          </button>
        </div>

        {/* Admin Settings Popup */}
        {showSettings && adminMode && (
          <div className="absolute bottom-full left-3 right-3 mb-2 bg-[#161B27] border border-[#1E2530] rounded-2xl shadow-2xl overflow-hidden z-50">
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
            <div className="flex border-b border-[#1E2530]">
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
            </div>

            {/* Tab: 내 계정 */}
            {activeTab === 'account' && (
              <div className="px-4 py-4 space-y-3">
                <div>
                  <label className="block text-[#8B9BB4] text-xs font-semibold mb-1.5">
                    <i className="ri-mail-line mr-1"></i>Admin 이메일
                  </label>
                  <div className="bg-[#0D1117] border border-[#1E2530] rounded-lg px-3 py-2 text-[#8B9BB4] text-xs font-mono">
                    {displayEmail}
                  </div>
                  <p className="text-[#4A5568] text-[10px] mt-1">이메일은 서버 DB 키라 변경할 수 없습니다.</p>
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
                      placeholder="변경할 비밀번호 (4자 이상)"
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
                  disabled={accountSaving}
                  className="w-full bg-[#00E5CC] text-[#0A0E1A] text-xs font-bold py-2 rounded-lg cursor-pointer hover:bg-[#00C9B1] transition-colors whitespace-nowrap disabled:opacity-60 disabled:cursor-not-allowed"
                >
                  {accountSaving ? '저장 중...' : '저장'}
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
                    disabled={addBusy}
                    className="w-full bg-[#00E5CC]/10 border border-[#00E5CC]/30 text-[#00E5CC] text-xs font-semibold py-2 rounded-lg cursor-pointer hover:bg-[#00E5CC]/20 transition-colors whitespace-nowrap disabled:opacity-60 disabled:cursor-not-allowed"
                  >
                    <i className="ri-add-line mr-1"></i>
                    {addBusy ? '추가 중...' : '계정 추가'}
                  </button>
                </div>

                {/* 유저 목록 */}
                <div>
                  <p className="text-[#4A5568] text-xs font-semibold mb-2">
                    <i className="ri-group-line mr-1"></i>
                    접속 가능 계정 ({users.length}명)
                    {usersLoading && <i className="ri-loader-4-line animate-spin ml-1 text-[10px]"></i>}
                  </p>
                  {users.length === 0 && !usersLoading ? (
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
                            <p className="text-[#4A5568] text-[10px]">추가일: {u.createdAt?.slice(0, 10)}</p>
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
          </div>
        )}
      </div>
    </aside>
  );
}
