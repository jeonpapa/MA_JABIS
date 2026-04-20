import { api } from './client';

export interface KeytrudaIndication {
  id: string;
  disease: string;
  disease_kr: string;
  line_of_therapy: string;
  stage: string;
  biomarker_class: string;
  title: string;
  pivotal_trial: string | null;
  mfds_approved: boolean;
  mfds_date: string | null;
  fda_date: string | null;
  is_reimbursed: boolean;
  reimbursement_effective_date: string | null;
  reimbursement_criteria: string | null;
  reimbursement_notice_date: string | null;
  reimbursement_notice_url: string | null;
}

export interface MsdSummary {
  reimbursedProductCount: number;
  latestApplyDate: string | null;
  keytruda: {
    totalIndications: number;
    mfdsApproved: number;
    pendingMfds: number;
    reimbursedIndications: number;
    pendingReimbursement: number;
    items: KeytrudaIndication[];
  };
}

interface RawMsdSummary {
  reimbursed_product_count: number;
  latest_apply_date: string | null;
  keytruda: {
    total_indications: number;
    mfds_approved: number;
    pending_mfds: number;
    reimbursed_indications: number;
    pending_reimbursement: number;
    items: KeytrudaIndication[];
  };
}

export async function fetchMsdSummary(): Promise<MsdSummary> {
  const raw = await api.get<RawMsdSummary>('/api/msd/summary');
  return {
    reimbursedProductCount: raw.reimbursed_product_count,
    latestApplyDate: raw.latest_apply_date,
    keytruda: {
      totalIndications: raw.keytruda.total_indications,
      mfdsApproved: raw.keytruda.mfds_approved,
      pendingMfds: raw.keytruda.pending_mfds,
      reimbursedIndications: raw.keytruda.reimbursed_indications,
      pendingReimbursement: raw.keytruda.pending_reimbursement,
      items: raw.keytruda.items,
    },
  };
}

export interface ReimbursedProduct {
  insurance_code: string;
  product_name: string;
  brand_name: string;
  ingredient: string;
  dosage_form: string;
  dosage_strength: string;
  max_price: number;
  coverage_start: string;
}

export interface ReimbursedProductsResponse {
  latestApplyDate: string | null;
  count: number;
  items: ReimbursedProduct[];
}

interface RawReimbursed {
  latest_apply_date: string | null;
  count: number;
  items: ReimbursedProduct[];
}

export async function fetchReimbursedProducts(): Promise<ReimbursedProductsResponse> {
  const raw = await api.get<RawReimbursed>('/api/msd/reimbursed-products');
  return { latestApplyDate: raw.latest_apply_date, count: raw.count, items: raw.items };
}
