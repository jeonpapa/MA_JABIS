import { api, ApiError, setToken, clearAuth, TOKEN_KEY, USER_KEY } from '@/api/client';

export const ADMIN_EMAIL = 'admin@marketintel.kr';

export interface AppUser {
  email: string;
  role: 'admin' | 'user';
  createdAt: string;
  lastLoginAt?: string | null;
}

export interface LoginResponse {
  token: string;
  user: AppUser;
}

export async function login(email: string, password: string): Promise<LoginResponse> {
  const res = await api.post<LoginResponse>(
    '/api/auth/login',
    { email: email.trim(), password },
    { auth: false }
  );
  setToken(res.token);
  localStorage.setItem(USER_KEY, res.user.email);
  localStorage.setItem('app_authed', '1');
  return res;
}

export function logout(): void {
  clearAuth();
}

export async function fetchMe(): Promise<AppUser | null> {
  try {
    const res = await api.get<{ user: AppUser }>('/api/auth/me');
    localStorage.setItem(USER_KEY, res.user.email);
    localStorage.setItem('app_authed', '1');
    return res.user;
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      clearAuth();
      return null;
    }
    throw e;
  }
}

export async function listUsers(): Promise<AppUser[]> {
  const res = await api.get<{ users: AppUser[] }>('/api/admin/users');
  return res.users;
}

export async function addUser(
  email: string,
  password: string,
  role: 'admin' | 'user' = 'user'
): Promise<{ ok: boolean; error?: string; user?: AppUser }> {
  try {
    const res = await api.post<{ user: AppUser }>('/api/admin/users', { email, password, role });
    return { ok: true, user: res.user };
  } catch (e) {
    if (e instanceof ApiError) return { ok: false, error: e.message };
    throw e;
  }
}

export async function removeUser(email: string): Promise<{ ok: boolean; error?: string }> {
  try {
    await api.delete<{ ok: true }>(`/api/admin/users/${encodeURIComponent(email)}`);
    return { ok: true };
  } catch (e) {
    if (e instanceof ApiError) return { ok: false, error: e.message };
    throw e;
  }
}

export async function updateMyPassword(
  newPassword: string
): Promise<{ ok: boolean; error?: string }> {
  try {
    await api.patch<{ ok: true }>('/api/auth/me/password', { newPassword });
    return { ok: true };
  } catch (e) {
    if (e instanceof ApiError) return { ok: false, error: e.message };
    throw e;
  }
}

export function isAdmin(email: string): boolean {
  return email.toLowerCase() === ADMIN_EMAIL.toLowerCase();
}

export function getCurrentUser(): string {
  return localStorage.getItem(USER_KEY) || '';
}

export function hasToken(): boolean {
  return Boolean(localStorage.getItem(TOKEN_KEY));
}
