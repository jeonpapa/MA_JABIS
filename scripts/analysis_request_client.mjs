#!/usr/bin/env node
/** 분석 요청 워커 전송 도구 (Aside). 의존성 없음. 분석·메일은 하지 않는다.
 * 자격증명은 config/.env 의 PRICE_AGENT_EMAIL/PRICE_AGENT_PASSWORD(price_agent) 만. JWT·lease 를 출력하지 않는다.
 * claim 은 lease 를 --state 경로(세션 tmp, 0600)에 저장한다.
 *
 *   node scripts/analysis_request_client.mjs queue
 *   node scripts/analysis_request_client.mjs claim ID --state TMP/lease.json
 *   node scripts/analysis_request_client.mjs heartbeat ID --state TMP/lease.json
 *   node scripts/analysis_request_client.mjs ask ID --state TMP/lease.json --file q.json        {"question": "..."}
 *   node scripts/analysis_request_client.mjs file ID --state TMP/lease.json --path result.xlsx
 *   node scripts/analysis_request_client.mjs complete ID --state TMP/lease.json --file result.json
 *   node scripts/analysis_request_client.mjs fail ID --state TMP/lease.json --file reason.json   {"reason": "..."}
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const [command, ...rest] = process.argv.slice(2);
const flag = name => { const i = rest.indexOf(name); return i < 0 ? null : rest[i + 1]; };
const pos = rest.filter((v, i) => !v.startsWith('--') && !(i > 0 && rest[i - 1].startsWith('--')));
let cfg = {};
try {
  for (const line of (await fs.readFile(path.join(root, 'config/.env'), 'utf8')).split('\n')) {
    const m = line.match(/^\s*([A-Z_]+)\s*=\s*(.*?)\s*$/);
    if (m) cfg[m[1]] = m[2].replace(/^(['"])(.*)\1$/, '$2');
  }
} catch (e) { if (e.code !== 'ENOENT') throw new Error('Cannot read config/.env'); }
const get = key => process.env[key] || cfg[key];
const base = (get('PRICE_API_BASE_URL') || 'https://ma-ai-dossier.fly.dev').replace(/\/$/, '');
const u = new URL(base);
if (u.username || u.password || (u.protocol !== 'https:' && !['localhost', '127.0.0.1'].includes(u.hostname))) throw new Error('Unsafe PRICE_API_BASE_URL');
async function http(endpoint, method = 'GET', body, token, lease) {
  const res = await fetch(base + endpoint, {method, signal: AbortSignal.timeout(180000), headers: {
    ...(token ? {Authorization: `Bearer ${token}`} : {}),
    ...(lease ? {'X-Analysis-Lease': lease} : {}),
    ...(body === undefined ? {} : {'Content-Type': 'application/json'}),
  }, body: body === undefined ? undefined : JSON.stringify(body)});
  let value; try { value = await res.json(); } catch { throw new Error(`Non-JSON API response (${res.status})`); }
  if (!res.ok) throw new Error(`API ${res.status}: ${String(value.error || 'request failed').slice(0, 300)}`);
  return value;
}
async function main() {
  const cmds = ['queue', 'claim', 'heartbeat', 'ask', 'file', 'complete', 'fail'];
  if (!cmds.includes(command)) throw new Error('Commands: ' + cmds.join(' | '));
  const email = get('PRICE_AGENT_EMAIL'), password = get('PRICE_AGENT_PASSWORD');
  if (!email || !password) throw new Error('Setup required: PRICE_AGENT_EMAIL and PRICE_AGENT_PASSWORD in config/.env');
  const login = await http('/api/auth/login', 'POST', {email, password});
  if (login.user?.role !== 'price_agent') throw new Error('Use the narrowly scoped price_agent account');
  const token = login.token;
  if (command === 'queue') return http('/api/analysis-agent/requests', 'GET', undefined, token);
  const rid = pos[0];
  if (!/^[a-f0-9]{32}$/.test(rid || '')) throw new Error('Invalid request ID');
  const statePath = flag('--state');
  if (!statePath) throw new Error('--state temporary path is required');
  const endpoint = `/api/analysis-agent/requests/${rid}`;
  if (command === 'claim') {
    const reserved = await fs.open(statePath, 'wx', 0o600); await reserved.close();
    try {
      const v = await http(endpoint + '/claim', 'POST', {}, token);
      await fs.writeFile(statePath, JSON.stringify({request_id: rid, lease_token: v.lease_token, lease_until: v.lease_until}), {mode: 0o600});
      return {request: v.request, lease_until: v.lease_until};
    } catch (e) { await fs.unlink(statePath).catch(() => {}); throw e; }
  }
  const state = JSON.parse(await fs.readFile(statePath, 'utf8'));
  if (state.request_id !== rid) throw new Error('Lease belongs to a different request');
  const lease = state.lease_token;
  if (command === 'heartbeat') return http(endpoint + '/heartbeat', 'POST', {}, token, lease);
  if (command === 'file') {
    const p = flag('--path'); if (!p) throw new Error('--path FILE required');
    const buf = await fs.readFile(p);
    return http(endpoint + '/files', 'POST', {filename: path.basename(p), content_b64: buf.toString('base64')}, token, lease);
  }
  const f = flag('--file'); if (!f) throw new Error('--file JSONFILE required');
  const payload = JSON.parse(await fs.readFile(f, 'utf8'));
  const route = {ask: '/ask', complete: '/complete', fail: '/fail'}[command];
  return http(endpoint + route, 'POST', payload, token, lease);
}
try { console.log(JSON.stringify(await main(), null, 2)); }
catch (e) { console.error(e.message); process.exitCode = 1; }
