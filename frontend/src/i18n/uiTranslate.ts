// UI 언어 토글 (2026-11-10) — 화면 문구만 영어로. React 컴포넌트를 고치지 않고 DOM 텍스트·속성을 사전(uiEn.ts)으로 바꾼다.
// - 데이터(약제명·기사·보고서)는 사전에 없으므로 그대로 한국어로 남는다.
// - React 가 다시 그리며 한국어로 되돌리면 MutationObserver 가 다시 바꾼다(영어 문구는 사전에 없어 반복되지 않는다).
// - 한국어로 돌아가면 바꾼 텍스트·속성을 원래 값으로 복원한다.
import { translateUi } from './uiEn';

export type UiLang = 'ko' | 'en';
const KEY = 'ui_lang';
const ATTRS = ['placeholder', 'title', 'aria-label'];
const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'TEXTAREA', 'CODE', 'PRE']);
export const UI_LANG_EVENT = 'ui-lang-changed';

const textOrig = new Map<Text, string>();
const attrOrig = new Map<Element, Record<string, string>>();
let observer: MutationObserver | null = null;

export function getUiLang(): UiLang {
  try { return localStorage.getItem(KEY) === 'en' ? 'en' : 'ko'; } catch { return 'ko'; }
}

function skip(node: Node | null): boolean {
  for (let n = node; n; n = n.parentNode) {
    if (n.nodeType === 1) {
      const el = n as Element;
      if (SKIP_TAGS.has(el.tagName) || el.getAttribute('data-no-translate') !== null || (el as HTMLElement).isContentEditable) return true;
    }
  }
  return false;
}

function doText(n: Text) {
  const v = n.nodeValue || '';
  const out = translateUi(v);
  if (out === null || out === v || skip(n.parentNode)) return;
  if (!textOrig.has(n)) textOrig.set(n, v);
  n.nodeValue = out;
}

function doAttrs(el: Element) {
  for (const a of ATTRS) {
    const v = el.getAttribute(a);
    if (!v) continue;
    const out = translateUi(v);
    if (out === null || out === v) continue;
    const rec = attrOrig.get(el) || {};
    if (!(a in rec)) rec[a] = v;
    attrOrig.set(el, rec);
    el.setAttribute(a, out);
  }
}

function walk(root: Node) {
  if (root.nodeType === 3) { doText(root as Text); return; }
  if (root.nodeType !== 1 || skip(root)) return;
  doAttrs(root as Element);
  const tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
  let n: Node | null = tw.nextNode();
  while (n) {
    if (n.nodeType === 3) doText(n as Text); else doAttrs(n as Element);
    n = tw.nextNode();
  }
}

function start() {
  if (observer) return;
  walk(document.body);
  observer = new MutationObserver(muts => {
    for (const m of muts) {
      if (m.type === 'characterData') doText(m.target as Text);
      else if (m.type === 'attributes') doAttrs(m.target as Element);
      else m.addedNodes.forEach(walk);
    }
  });
  observer.observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS });
}

function stop() {
  observer?.disconnect();
  observer = null;
  textOrig.forEach((v, n) => { if (n.isConnected) n.nodeValue = v; });
  attrOrig.forEach((rec, el) => { if (el.isConnected) Object.entries(rec).forEach(([a, v]) => el.setAttribute(a, v)); });
  textOrig.clear();
  attrOrig.clear();
}

export function setUiLang(lang: UiLang) {
  try { localStorage.setItem(KEY, lang); } catch { /* 저장 불가 */ }
  document.documentElement.lang = lang === 'en' ? 'en' : 'ko';
  if (lang === 'en') start(); else stop();
  window.dispatchEvent(new CustomEvent(UI_LANG_EVENT, { detail: lang }));
}

export function initUiLang() {
  if (getUiLang() === 'en') {
    document.documentElement.lang = 'en';
    if (document.body) start(); else window.addEventListener('DOMContentLoaded', start, { once: true });
  }
}
