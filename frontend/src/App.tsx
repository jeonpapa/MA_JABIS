import { useState, useEffect } from "react";
import { BrowserRouter, useLocation, Navigate } from "react-router-dom";
import { AppRoutes } from "./router";
import { I18nextProvider } from "react-i18next";
import i18n from "./i18n";
import Sidebar from "@/components/feature/Sidebar";
import { hasToken, fetchMe } from "@/utils/authUsers";

function AuthGuard({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const isLoginPage = location.pathname === '/login';

  // 동기 게이트: 현재 토큰 보유 여부를 매 렌더마다 읽음 → login() 직후 즉시 반영
  const authed = hasToken();

  // 마운트 시 1회 토큰 검증. 만료/위조 토큰이면 fetchMe 가 clearAuth → 강제 재렌더로 /login.
  const [, forceTick] = useState(0);
  useEffect(() => {
    let cancelled = false;
    if (hasToken()) {
      fetchMe()
        .then(user => {
          if (!cancelled && !user) forceTick(t => t + 1);
        })
        .catch(() => {/* 네트워크 오류 시 세션 유지 (API 호출이 자체적으로 401 처리) */});
    }
    return () => { cancelled = true; };
  }, []);

  if (!authed && !isLoginPage) {
    return <Navigate to="/login" replace />;
  }
  if (authed && isLoginPage) {
    return <Navigate to="/" replace />;
  }
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
