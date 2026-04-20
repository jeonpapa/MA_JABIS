import { api } from './client';

export interface Keyword {
  id: number;
  text: string;
  weight: number;
  color: string | null;
  created_at: string;
  updated_at: string;
}

export interface KeywordInput {
  text: string;
  weight?: number;
  color?: string | null;
}

export async function listKeywords(): Promise<Keyword[]> {
  const r = await api.get<{ items: Keyword[] }>('/api/keyword-cloud');
  return r.items;
}

export async function createKeyword(input: KeywordInput): Promise<Keyword> {
  const r = await api.post<{ item: Keyword }>('/api/admin/keyword-cloud', input);
  return r.item;
}

export async function updateKeyword(id: number, patch: Partial<KeywordInput>): Promise<Keyword> {
  const r = await api.patch<{ item: Keyword }>(`/api/admin/keyword-cloud/${id}`, patch);
  return r.item;
}

export async function deleteKeyword(id: number): Promise<void> {
  await api.delete<{ ok: true }>(`/api/admin/keyword-cloud/${id}`);
}
