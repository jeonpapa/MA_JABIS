import { api, getToken } from './client';

export type CountryCode = 'JP' | 'IT' | 'FR' | 'CH' | 'UK' | 'DE' | 'US';

export const ALL_COUNTRIES: CountryCode[] = ['JP', 'IT', 'FR', 'CH', 'UK', 'DE', 'US'];

export interface CountryAssumption {
  currency: string;
  factory_ratio: number;
  vat_rate: number;
  margin_rate: number;
  fx_rate_default: number;
  phase?: number;
}

export interface Assumptions {
  fx_window_months: number;
  fx_source: string;
  countries: Record<string, CountryAssumption>;
  last_updated?: string;
  updated_by?: string;
}

export interface ScenarioSpec {
  name: string;
  include_countries: string[];
  formula: 'min_n' | 'avg_n';
  percent: number;
  notes?: string;
  fx_override?: number | null;
}

export interface ComputedRow {
  local_price: number;
  raw_local_price?: number;
  mg_pack_total?: number | null;
  price_per_mg?: number | null;
  dose_confidence?: 'parsed' | 'reference' | 'combo' | null;
  fx_rate: number;
  krw_converted: number;
  factory_ratio: number;
  factory_krw: number;
  vat_rate: number;
  vat_applied: number;
  margin_rate: number;
  adjusted: number;
}

export interface ComputedStats {
  min: number;
  avg: number;
  min_country?: string;
  min_percent: number;
  avg_percent: number;
  percent: number;
}

export interface ComputedScenario {
  name: string;
  formula: string;
  percent: number;
  notes?: string;
  include_countries: string[];
  rows: Record<string, ComputedRow>;
  excluded: Record<string, string>;
  stats: ComputedStats;
  proposed_ceiling: number;
  basis: string;
  reference_mg?: number | null;
}

export interface HTASummaryAgency {
  name: string;
  code: string;
  country?: string;
  agree: number;
  conflict: number;
  single: number;
  narrative: number;
  missing: number;
}

export interface HTASummary {
  total_fields: number;
  agree: number;
  conflict: number;
  agencies: HTASummaryAgency[];
}

export interface DomesticRow {
  product_name_kr?: string;
  dosage_strength?: string;
  atc_code?: string;
  manufacturer?: string;
  [k: string]: unknown;
}

export interface ForeignRow {
  product_name?: string;
  form?: string;
  strength?: string;
  pack?: string;
  local_price?: number | null;
  currency?: string;
  source_site?: string;
  source_url?: string;
  searched_at?: string;
  [k: string]: unknown;
}

export interface ResolveResult {
  query: string;
  ingredient: string;
  products: string[];
  source: string;
}

export interface MatchingRow {
  country: string;
  source: string;
  product_name: string;
  form: string;
  strength: string;
  pack: string;
  raw_price: number | null;
  currency: string;
  grade: string;
  searched_at?: string;
}

export interface ProductInfo {
  drug_name_en: string;
  drug_name_kr: string;
  ingredient: string;
  sku: string;
  atc: string;
  manufacturer: string;
}

export interface ComputeRequest {
  prices: Record<string, number>;
  scenarios: ScenarioSpec[];
  assumptions?: Assumptions;
  rows_meta?: Record<string, { product_name?: string; strength?: string; pack?: string; form?: string }>;
  product_slug?: string;
  reference_mg?: number | null;
}

export interface ComputeResponse {
  scenarios: ComputedScenario[];
  hta_summary: HTASummary | null;
}

export interface ExportSession {
  project: Record<string, unknown>;
  prices: Record<string, number>;
  scenarios: ComputedScenario[];
  selected: string;
  source_raw: unknown[];
  matching: unknown[];
  hta: unknown | null;
  audit_log: unknown[];
  assumptions?: Assumptions;
}

export async function fetchAssumptions(): Promise<Assumptions> {
  return api.get<Assumptions>('/api/workbench/assumptions');
}

export async function saveAssumptions(data: Assumptions): Promise<{ ok: boolean; saved: Assumptions }> {
  return api.put<{ ok: boolean; saved: Assumptions }>('/api/workbench/assumptions', data);
}

export async function fetchDefaults(): Promise<Assumptions> {
  return api.get<Assumptions>('/api/workbench/defaults');
}

export async function resolveDrug(q: string): Promise<ResolveResult> {
  return api.get<ResolveResult>(`/api/drug/resolve?q=${encodeURIComponent(q)}`);
}

export async function searchDomestic(q: string, limit = 5): Promise<{ results: DomesticRow[] }> {
  return api.get<{ results: DomesticRow[] }>(`/api/domestic/search?q=${encodeURIComponent(q)}&limit=${limit}`);
}

export async function fetchForeignCached(q: string): Promise<{ results: Record<string, ForeignRow[]> }> {
  return api.get<{ results: Record<string, ForeignRow[]> }>(`/api/foreign/cached?q=${encodeURIComponent(q)}`);
}

export async function runLiveForeign(query: string): Promise<{ error?: string }> {
  return api.post<{ error?: string }>('/api/foreign/search', { query, use_cache: false });
}

export async function computeScenarios(body: ComputeRequest): Promise<ComputeResponse> {
  return api.post<ComputeResponse>('/api/workbench/compute', body);
}

export async function fetchHTA(product: string): Promise<{ product: string; data: unknown; summary: HTASummary }> {
  return api.get<{ product: string; data: unknown; summary: HTASummary }>(
    `/api/workbench/hta?product=${encodeURIComponent(product)}`,
  );
}

export async function exportWorkbook(session: ExportSession): Promise<{ blob: Blob; filename: string }> {
  const token = getToken();
  const res = await fetch('/api/workbench/export', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(session),
  });
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { const j = await res.json(); if (j?.error) msg = j.error; } catch { /* ignore */ }
    throw new Error(msg);
  }
  const blob = await res.blob();
  const filename =
    res.headers.get('Content-Disposition')?.match(/filename="?([^";]+)"?/)?.[1] ||
    `workbench_${Date.now()}.xlsx`;
  return { blob, filename };
}

export function parseReferenceMg(sku?: string | null): number | null {
  if (!sku) return null;
  const m = String(sku).match(/(\d+(?:\.\d+)?)\s*mg/i);
  return m ? parseFloat(m[1]) : null;
}

export function countCached(cached: { results?: Record<string, ForeignRow[]> } | null): number {
  if (!cached?.results) return 0;
  return Object.values(cached.results).reduce((n, rows) => n + (rows?.length || 0), 0);
}
