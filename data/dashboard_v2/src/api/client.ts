export const TOKEN_KEY = 'app_jwt';
export const USER_KEY = 'app_current_user';

export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(message: string, status: number, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export function clearAuth(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  localStorage.removeItem('app_authed');
  localStorage.removeItem('app_auto_login');
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  options: { auth?: boolean } = { auth: true }
): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (options.auth !== false) {
    const token = getToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;
  }
  const res = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data: any = null;
  const text = await res.text();
  if (text) {
    try { data = JSON.parse(text); } catch { data = text; }
  }
  if (!res.ok) {
    const message = (data && data.error) || `HTTP ${res.status}`;
    const code = data && data.code;
    if (res.status === 401 && options.auth !== false) {
      clearAuth();
    }
    throw new ApiError(message, res.status, code);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string, opts?: { auth?: boolean }) => request<T>('GET', path, undefined, opts),
  post: <T>(path: string, body?: unknown, opts?: { auth?: boolean }) => request<T>('POST', path, body, opts),
  put: <T>(path: string, body?: unknown, opts?: { auth?: boolean }) => request<T>('PUT', path, body, opts),
  patch: <T>(path: string, body?: unknown, opts?: { auth?: boolean }) => request<T>('PATCH', path, body, opts),
  delete: <T>(path: string, opts?: { auth?: boolean }) => request<T>('DELETE', path, undefined, opts),
};
