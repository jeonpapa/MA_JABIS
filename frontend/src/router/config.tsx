import { RouteObject } from 'react-router-dom';
import HomePage from '@/pages/home/page';
import DomesticPricingPage from '@/pages/domestic-pricing/page';
import RegimenCostPage from '@/pages/regimen-cost/page';
import InternationalPricingPage from '@/pages/international-pricing/page';
import MarketSharePage from '@/pages/market-share/page';
import CompetitorTrendsPage from '@/pages/competitor-trends/page';
import DailyMailingPage from '@/pages/daily-mailing/page';
import ReimbursementStatusPage from '@/pages/reimbursement-status/page';
import PolicyIntelligencePage from '@/pages/policy-intelligence/page';
import AnalogSearchPage from '@/pages/analog-search/page';
import LoginPage from '@/pages/login/page';
import NotFound from '@/pages/NotFound';
// admin — 각 페이지가 fetchMe() 로 role 셀프 가드 (v2 패턴)
import AdminMarketSharePage from '@/pages/admin/market-share/page';
import AdminMsdPipelinePage from '@/pages/admin/msd-pipeline/page';
import AdminCompetitorTrendsPage from '@/pages/admin/competitor-trends/page';
import AdminReimbursementPage from '@/pages/admin/reimbursement/page';
import AdminReimbursementPipelinePage from '@/pages/admin/reimbursement-pipeline/page';
import AdminDailyMailingKanbanPage from '@/pages/admin/daily-mailing-kanban/page';

const routes: RouteObject[] = [
  { path: '/login', element: <LoginPage /> },
  { path: '/', element: <HomePage /> },
  { path: '/domestic-pricing', element: <DomesticPricingPage /> },
  { path: '/regimen-cost', element: <RegimenCostPage /> },
  { path: '/international-pricing', element: <InternationalPricingPage /> },
  { path: '/market-share', element: <MarketSharePage /> },
  { path: '/competitor-trends', element: <CompetitorTrendsPage /> },
  { path: '/daily-mailing', element: <DailyMailingPage /> },
  { path: '/reimbursement-status', element: <ReimbursementStatusPage /> },
  { path: '/policy-intelligence', element: <PolicyIntelligencePage /> },
  { path: '/analog-search', element: <AnalogSearchPage /> },
  { path: '/admin/market-share', element: <AdminMarketSharePage /> },
  { path: '/admin/msd-pipeline', element: <AdminMsdPipelinePage /> },
  { path: '/admin/competitor-trends', element: <AdminCompetitorTrendsPage /> },
  { path: '/admin/reimbursement', element: <AdminReimbursementPage /> },
  { path: '/admin/reimbursement-pipeline', element: <AdminReimbursementPipelinePage /> },
  { path: '/admin/daily-mailing-kanban', element: <AdminDailyMailingKanbanPage /> },
  { path: '*', element: <NotFound /> },
];

export default routes;
