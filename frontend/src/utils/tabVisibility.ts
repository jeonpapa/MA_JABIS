const STORAGE_KEY = 'app_tab_visibility';

export interface TabVisibility {
  [path: string]: boolean;
}

const DEFAULT_VISIBILITY: TabVisibility = {
  '/': true,
  '/domestic-pricing': true,
  '/international-pricing': true,
  '/market-share': true,
  '/competitor-trends': true,
  '/daily-mailing': true,
  '/reimbursement-status': true,
};

export function getTabVisibility(): TabVisibility {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
      const parsed = JSON.parse(stored);
      return { ...DEFAULT_VISIBILITY, ...parsed };
    }
  } catch {
    // ignore
  }
  return { ...DEFAULT_VISIBILITY };
}

export function setTabVisibility(path: string, visible: boolean): void {
  const current = getTabVisibility();
  current[path] = visible;
  localStorage.setItem(STORAGE_KEY, JSON.stringify(current));
}

export function isTabVisible(path: string): boolean {
  const visibility = getTabVisibility();
  return visibility[path] !== false;
}