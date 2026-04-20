import { RouteObject } from 'react-router-dom';
import HomePage from '@/pages/home/page';
import DomesticPricingPage from '@/pages/domestic-pricing/page';
import InternationalPricingPage from '@/pages/international-pricing/page';
import MarketSharePage from '@/pages/market-share/page';
import AdminMarketSharePage from '@/pages/admin/market-share/page';
import AdminMsdPipelinePage from '@/pages/admin/msd-pipeline/page';
import AdminBrandTrafficPage from '@/pages/admin/brand-traffic/page';
import AdminCompetitorTrendsPage from '@/pages/admin/competitor-trends/page';
import AdminKeywordCloudPage from '@/pages/admin/keyword-cloud/page';
import AdminReimbursementPage from '@/pages/admin/reimbursement/page';
import CompetitorTrendsPage from '@/pages/competitor-trends/page';
import DailyMailingPage from '@/pages/daily-mailing/page';
import WorkbenchPage from '@/pages/workbench/page';
import AdminWorkbenchSettingsPage from '@/pages/admin/workbench-settings/page';
import LoginPage from '@/pages/login/page';
import NotFound from '@/pages/NotFound';

const routes: RouteObject[] = [
  { path: '/login', element: <LoginPage /> },
  { path: '/', element: <HomePage /> },
  { path: '/domestic-pricing', element: <DomesticPricingPage /> },
  { path: '/international-pricing', element: <InternationalPricingPage /> },
  { path: '/market-share', element: <MarketSharePage /> },
  { path: '/competitor-trends', element: <CompetitorTrendsPage /> },
  { path: '/admin/market-share', element: <AdminMarketSharePage /> },
  { path: '/admin/msd-pipeline', element: <AdminMsdPipelinePage /> },
  { path: '/admin/brand-traffic', element: <AdminBrandTrafficPage /> },
  { path: '/admin/competitor-trends', element: <AdminCompetitorTrendsPage /> },
  { path: '/admin/keyword-cloud', element: <AdminKeywordCloudPage /> },
  { path: '/admin/reimbursement', element: <AdminReimbursementPage /> },
  { path: '/daily-mailing', element: <DailyMailingPage /> },
  { path: '/workbench', element: <WorkbenchPage /> },
  { path: '/admin/workbench-settings', element: <AdminWorkbenchSettingsPage /> },
  { path: '*', element: <NotFound /> },
];

export default routes;
