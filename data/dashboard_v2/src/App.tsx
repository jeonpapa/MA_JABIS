import { useEffect, useState } from "react";
import { BrowserRouter, useLocation, useNavigate } from "react-router-dom";
import { AppRoutes } from "./router";
import { I18nextProvider } from "react-i18next";
import i18n from "./i18n";
import Sidebar from "@/components/feature/Sidebar";
import { fetchMe, hasToken } from "@/utils/authUsers";

function AuthGuard({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const isLoginPage = location.pathname === '/login';
  const [state, setState] = useState<'checking' | 'authed' | 'anon'>(
    hasToken() ? 'checking' : 'anon'
  );

  useEffect(() => {
    let cancelled = false;
    if (hasToken()) {
      fetchMe()
        .then(user => {
          if (cancelled) return;
          setState(user ? 'authed' : 'anon');
        })
        .catch(() => { if (!cancelled) setState('anon'); });
    } else {
      setState('anon');
    }
    return () => { cancelled = true; };
  }, [location.pathname]);

  useEffect(() => {
    if (state === 'anon' && !isLoginPage) navigate('/login', { replace: true });
    if (state === 'authed' && isLoginPage) navigate('/', { replace: true });
  }, [state, isLoginPage, navigate]);

  if (state === 'checking') {
    return (
      <div className="min-h-screen bg-[#0D1117] flex items-center justify-center">
        <div className="flex items-center gap-2 text-[#8B9BB4] text-sm">
          <i className="ri-loader-4-line animate-spin"></i>
          인증 확인 중...
        </div>
      </div>
    );
  }
  if (state === 'anon' && !isLoginPage) return null;
  if (state === 'authed' && isLoginPage) return null;
  return <>{children}</>;
}

function Layout() {
  const location = useLocation();
  const isLoginPage = location.pathname === '/login';

  if (isLoginPage) {
    return <AppRoutes />;
  }

  return (
    <div className="flex min-h-screen bg-[#0D1117]">
      <Sidebar />
      <main className="flex-1 ml-60 min-h-screen overflow-y-auto">
        <AppRoutes />
      </main>
    </div>
  );
}

function App() {
  return (
    <I18nextProvider i18n={i18n}>
      <BrowserRouter basename={__BASE_PATH__}>
        <AuthGuard>
          <Layout />
        </AuthGuard>
      </BrowserRouter>
    </I18nextProvider>
  );
}

export default App;
