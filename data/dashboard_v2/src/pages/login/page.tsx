import { useState, FormEvent, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { login, fetchMe, ADMIN_EMAIL, hasToken } from '@/utils/authUsers';

export default function LoginPage() {
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [rememberMe, setRememberMe] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const autoLogin = localStorage.getItem('app_auto_login') === '1';
    if (autoLogin && hasToken()) {
      fetchMe().then(user => {
        if (user) navigate('/');
      });
    }
  }, [navigate]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await login(email, password);
      if (rememberMe) localStorage.setItem('app_auto_login', '1');
      else localStorage.removeItem('app_auto_login');
      navigate('/');
    } catch (err: any) {
      setError(err?.message || '이메일 또는 비밀번호가 올바르지 않습니다.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#0D1117] flex items-center justify-center relative overflow-hidden">
      {/* 배경 글로우 */}
      <div className="absolute inset-0 pointer-events-none">
        <div className="absolute top-1/4 left-1/4 w-96 h-96 rounded-full bg-[#00E5CC]/5 blur-3xl"></div>
        <div className="absolute bottom-1/4 right-1/4 w-80 h-80 rounded-full bg-[#7C3AED]/5 blur-3xl"></div>
      </div>

      <div className="relative z-10 w-full max-w-sm px-6">
        {/* Logo */}
        <div className="flex flex-col items-center mb-10">
          <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-[#00E5CC]/20 to-[#7C3AED]/20 border border-[#1E2530] flex items-center justify-center mb-4">
            <img
              src="https://public.readdy.ai/ai/img_res/be30245e-c610-43e9-9d9b-6faaf65094e2.png"
              alt="Logo"
              className="w-8 h-8 object-contain"
            />
          </div>
          <h1 className="text-white text-2xl font-bold tracking-tight">Market Intel</h1>
          <p className="text-[#8B9BB4] text-sm mt-1">Market Access Hub</p>
        </div>

        {/* Login Card */}
        <div className="bg-[#161B27] border border-[#1E2530] rounded-2xl p-8">
          <h2 className="text-white text-lg font-bold mb-1">로그인</h2>
          <p className="text-[#4A5568] text-xs mb-6">이메일과 비밀번호를 입력하여 접속하세요</p>

          <form onSubmit={handleSubmit} className="space-y-4">
            {/* 이메일 */}
            <div>
              <label className="block text-[#8B9BB4] text-xs font-semibold mb-1.5">이메일</label>
              <div className="relative">
                <span className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 flex items-center justify-center text-[#4A5568]">
                  <i className="ri-mail-line text-sm"></i>
                </span>
                <input
                  type="email"
                  value={email}
                  onChange={e => { setEmail(e.target.value); setError(''); }}
                  required
                  placeholder="이메일 주소 입력"
                  className="w-full bg-[#0D1117] border border-[#1E2530] rounded-xl pl-9 pr-4 py-3 text-white text-sm placeholder-[#4A5568] focus:outline-none focus:border-[#00E5CC]/60 transition-colors"
                />
              </div>
            </div>

            {/* 비밀번호 */}
            <div>
              <label className="block text-[#8B9BB4] text-xs font-semibold mb-1.5">비밀번호</label>
              <div className="relative">
                <span className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 flex items-center justify-center text-[#4A5568]">
                  <i className="ri-lock-line text-sm"></i>
                </span>
                <input
                  type={showPw ? 'text' : 'password'}
                  value={password}
                  onChange={e => { setPassword(e.target.value); setError(''); }}
                  required
                  placeholder="비밀번호 입력"
                  className="w-full bg-[#0D1117] border border-[#1E2530] rounded-xl pl-9 pr-10 py-3 text-white text-sm placeholder-[#4A5568] focus:outline-none focus:border-[#00E5CC]/60 transition-colors"
                />
                <button
                  type="button"
                  onClick={() => setShowPw(v => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 w-5 h-5 flex items-center justify-center text-[#4A5568] hover:text-white cursor-pointer transition-colors"
                >
                  <i className={`${showPw ? 'ri-eye-off-line' : 'ri-eye-line'} text-sm`}></i>
                </button>
              </div>
            </div>

            {/* 자동 로그인 */}
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setRememberMe(v => !v)}
                className={`w-4 h-4 rounded flex items-center justify-center border transition-all cursor-pointer flex-shrink-0 ${
                  rememberMe
                    ? 'bg-[#00E5CC] border-[#00E5CC]'
                    : 'bg-transparent border-[#2A3545] hover:border-[#00E5CC]/50'
                }`}
              >
                {rememberMe && <i className="ri-check-line text-[10px] text-[#0A0E1A] font-bold"></i>}
              </button>
              <span
                className="text-[#8B9BB4] text-xs cursor-pointer select-none"
                onClick={() => setRememberMe(v => !v)}
              >
                자동 로그인 (다음 접속 시 자동으로 로그인)
              </span>
            </div>

            {/* 에러 메시지 */}
            {error && (
              <div className="flex items-center gap-2 bg-red-400/10 border border-red-400/20 rounded-lg px-3 py-2.5">
                <span className="w-4 h-4 flex items-center justify-center text-red-400 flex-shrink-0">
                  <i className="ri-error-warning-line text-sm"></i>
                </span>
                <p className="text-red-400 text-xs">{error}</p>
              </div>
            )}

            {/* 접속 버튼 */}
            <button
              type="submit"
              disabled={loading}
              className="w-full bg-[#00E5CC] text-[#0A0E1A] font-bold py-3 rounded-xl cursor-pointer hover:bg-[#00C9B1] transition-colors text-sm whitespace-nowrap disabled:opacity-60 disabled:cursor-not-allowed flex items-center justify-center gap-2 mt-2"
            >
              {loading ? (
                <>
                  <i className="ri-loader-4-line text-sm animate-spin"></i>
                  접속 중...
                </>
              ) : (
                <>
                  <i className="ri-login-box-line text-sm"></i>
                  접속
                </>
              )}
            </button>
          </form>

          {/* 기본 계정 안내 */}
          <div className="mt-6 pt-5 border-t border-[#1E2530]">
            <p className="text-[#4A5568] text-xs text-center mb-2">초기 Admin 계정</p>
            <div className="bg-[#0D1117] rounded-lg px-3 py-2.5 space-y-1">
              <div className="flex items-center justify-between">
                <span className="text-[#4A5568] text-xs">이메일</span>
                <span className="text-[#8B9BB4] text-xs font-mono">{ADMIN_EMAIL}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-[#4A5568] text-xs">비밀번호</span>
                <span className="text-[#8B9BB4] text-xs font-mono">admin1234</span>
              </div>
            </div>
          </div>
        </div>

        <p className="text-center text-[#2A3545] text-xs mt-6">
          &copy; 2026 Market Intel · Market Access Hub
        </p>
      </div>
    </div>
  );
}
