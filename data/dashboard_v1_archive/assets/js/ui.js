/* MA AI Dossier — UI helpers
   페이지마다 분기된 toast / spinner / escape 헬퍼를 한 곳으로.
   components.css 의 .toast / .spinner 와 짝을 이룬다.

   사용:
     <script src="/dashboard/assets/js/ui.js"></script>
     UI.toast("저장 완료", "success");
     UI.escHtml(...)
*/
(function (global) {
  "use strict";

  /** alert() 대체. type: "info" | "success" | "error" (default error) */
  function toast(message, type, durationMs) {
    type = type || "error";
    durationMs = durationMs || 3500;
    const el = document.createElement("div");
    el.className = "toast" + (type === "success" ? " success" : type === "info" ? " info" : "");
    el.textContent = String(message);
    document.body.appendChild(el);
    setTimeout(function () {
      el.style.opacity = "0";
      setTimeout(function () { el.remove(); }, 250);
    }, durationMs);
  }

  /** HTML escape (innerHTML 주입 대비) */
  function escHtml(s) {
    if (s == null) return "";
    return String(s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  /** HTML attribute escape (onclick 인라인용) */
  function escAttr(s) {
    if (s == null) return "";
    return String(s).replace(/'/g, "\\'").replace(/"/g, "&quot;");
  }

  /** "5/12건" 형태 카운터 갱신. el = element or id */
  function setCount(el, filtered, total) {
    const node = (typeof el === "string") ? document.getElementById(el) : el;
    if (!node) return;
    node.innerHTML = total != null
      ? `<strong>${filtered}</strong>/${total}건`
      : `<strong>${filtered}</strong>건`;
  }

  /** 버튼/요소에 inline spinner + 라벨 토글
      옵션 setLoading(btn, true, "검색 중…") / setLoading(btn, false) */
  function setLoading(target, on, loadingLabel) {
    const node = (typeof target === "string") ? document.getElementById(target) : target;
    if (!node) return;
    if (on) {
      if (node._origHtml == null) node._origHtml = node.innerHTML;
      node.disabled = true;
      node.innerHTML = '<span class="spinner"></span>' + (loadingLabel || "로딩 중…");
    } else {
      node.disabled = false;
      if (node._origHtml != null) {
        node.innerHTML = node._origHtml;
        node._origHtml = null;
      }
    }
  }

  global.UI = { toast, escHtml, escAttr, setCount, setLoading };
})(window);
