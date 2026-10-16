#!/usr/bin/env node
/** Aside worker transport. No dependencies; no scraping or mail sending here.
 * Reusable credentials ONLY in config/.env (or injected environment).
 * Never print JWT/lease tokens. claim stores ephemeral lease at caller's --state
 * path (0600), which must be in a session temporary directory, never a repo.
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const [command, ...rest] = process.argv.slice(2);
const flag = name => { const i = rest.indexOf(name); return i < 0 ? null : rest[i + 1]; };
const pos = rest.filter((v, i) => !v.startsWith('--') && !(i > 0 && rest[i - 1].startsWith('--')));
const statePath = flag('--state');
let cfg = {};
try {
  for (const line of (await fs.readFile(path.join(root, 'config/.env'), 'utf8')).split('\n')) {
    const m = line.match(/^\s*([A-Z_]+)\s*=\s*(.*?)\s*$/);
    if (m) cfg[m[1]] = m[2].replace(/^(['"])(.*)\1$/, '$2');
  }
} catch (e) { if (e.code !== 'ENOENT') throw new Error('Cannot read config/.env'); }
const get = key => process.env[key] || cfg[key];
const base = (get('PRICE_API_BASE_URL') || 'https://ma-ai-dossier.fly.dev').replace(/\/$/, '');
const url = new URL(base);
if (url.username || url.password || (url.protocol !== 'https:' && !['localhost', '127.0.0.1'].includes(url.hostname))) throw new Error('Unsafe PRICE_API_BASE_URL');
async function http(endpoint, method = 'GET', body, token, lease) {
  const res = await fetch(base + endpoint, {method, signal: AbortSignal.timeout(120000), headers: {
    ...(token ? {Authorization: `Bearer ${token}`} : {}),
    ...(lease ? {'X-Price-Lease': lease} : {}),
    ...(body === undefined ? {} : {'Content-Type': 'application/json'}),
  }, body: body === undefined ? undefined : JSON.stringify(body)});
  let value; try { value = await res.json(); } catch { throw new Error(`Non-JSON API response (${res.status})`); }
  if (!res.ok) throw new Error(`API ${res.status}: ${String(value.error || 'request failed').slice(0,300)}`);
  return value;
}
async function main() {
  if (!['queue', 'claim', 'heartbeat', 'upload', 'readback', 'verify', 'notify'].includes(command)) throw new Error('Commands: queue | claim ID | heartbeat ID | upload ID CC | readback ID | verify ID | notify ID. Mutations require --state TEMPFILE; upload/verify/notify also --file JSONFILE.');
  const email = get('PRICE_AGENT_EMAIL'), password = get('PRICE_AGENT_PASSWORD');
  if (!email || !password) throw new Error('Setup required: PRICE_AGENT_EMAIL and PRICE_AGENT_PASSWORD in config/.env');
  const login = await http('/api/auth/login', 'POST', {email, password});
  if (login.user?.role !== 'price_agent') throw new Error('Use the narrowly scoped price_agent account, not admin or agent');
  const token = login.token;
  if (command === 'queue') return http('/api/price-agent/requests', 'GET', undefined, token);
  const rid = pos[0];
  if (!/^[a-f0-9]{32}$/.test(rid || '')) throw new Error('Invalid request ID');
  if (!statePath) throw new Error('--state temporary path is required');
  const endpoint = `/api/price-agent/requests/${rid}`;
  if (command === 'claim') {
    // Reserve storage before claiming. A preexisting state file must not destroy a live lease.
    const reserved = await fs.open(statePath, 'wx', 0o600); await reserved.close();
    try {
      const value = await http(endpoint + '/claim', 'POST', {}, token);
      await fs.writeFile(statePath, JSON.stringify({request_id:rid, lease_token:value.lease_token, lease_until:value.lease_until}), {mode:0o600});
      return {request:value.request, lease_until:value.lease_until};
    } catch (error) { await fs.unlink(statePath).catch(() => {}); throw error; }
  }
  const state = JSON.parse(await fs.readFile(statePath, 'utf8'));
  if (state.request_id !== rid) throw new Error('Lease belongs to a different request');
  if (command === 'readback') return http(endpoint + '/readback', 'GET', undefined, token, state.lease_token);
  if (command === 'heartbeat') return http(endpoint + '/heartbeat', 'POST', {}, token, state.lease_token);
  const file = flag('--file');
  if (!file) throw new Error('--file JSONFILE required');
  const payload = JSON.parse(await fs.readFile(file, 'utf8'));
  if (command === 'upload') {
    const cc = pos[1]; if (!/^[A-Z]{2}$/.test(cc || '')) throw new Error('Invalid country code');
    return http(endpoint + '/countries/' + cc, 'PUT', payload, token, state.lease_token);
  }
  return http(endpoint + (command === 'verify' ? '/verify' : '/notification'), 'POST', payload, token, state.lease_token);
}
try { console.log(JSON.stringify(await main(), null, 2)); }
catch (e) { console.error(e.message); process.exitCode = 1; }
