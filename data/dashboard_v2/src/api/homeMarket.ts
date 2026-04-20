import { api } from './client';

export interface TopPriceChangeItem {
  insurance_code: string;
  product_name: string;
  brand_name: string;
  ingredient: string;
  company: string;
  dosage_form: string;
  prev_price: number;
  curr_price: number;
  delta: number;
  delta_pct: number;
  variant_count: number;
  remark: string;
}

export interface TopPriceChangesResponse {
  latestApplyDate: string | null;
  prevApplyDate: string | null;
  count: number;
  items: TopPriceChangeItem[];
}

interface RawResp {
  latest_apply_date: string | null;
  prev_apply_date: string | null;
  count: number;
  items: TopPriceChangeItem[];
}

export async function fetchTopPriceChanges(limit = 10): Promise<TopPriceChangesResponse> {
  const raw = await api.get<RawResp>(`/api/home/top-price-changes?limit=${limit}`);
  return {
    latestApplyDate: raw.latest_apply_date,
    prevApplyDate: raw.prev_apply_date,
    count: raw.count,
    items: raw.items,
  };
}
