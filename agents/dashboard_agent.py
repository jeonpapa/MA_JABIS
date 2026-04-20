"""
대쉬보드 관리 에이전트 (v1 legacy — React SPA (dashboard_v2) 로 대체됨).
- 각 에이전트가 생성한 Markdown 소스 파일을 통합하여 HTML 대쉬보드를 렌더링한다.
- 출력 위치는 data/dashboard_v1_archive/ (config/settings.json → dashboard.output_dir).
- 보존 목적으로 유지 — 신규 UI 작업은 data/dashboard_v2/ 에서 진행.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────
# HTML 템플릿
# ────────────────────────────────────────────────────────────────────────────

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>MA AI 대쉬보드 | 약가 모니터링</title>
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <style>
    :root {{
      --primary: #1a56db;
      --primary-light: #e8f0fe;
      --success: #057a55;
      --warning: #c27803;
      --danger: #c81e1e;
      --gray-50: #f9fafb;
      --gray-100: #f3f4f6;
      --gray-200: #e5e7eb;
      --gray-700: #374151;
      --gray-900: #111827;
      --border: 1px solid var(--gray-200);
    }}

    * {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      font-size: 14px;
      color: var(--gray-900);
      background: var(--gray-50);
    }}

    /* ── 헤더 ── */
    header {{
      background: var(--primary);
      color: white;
      padding: 16px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }}
    header h1 {{ font-size: 18px; font-weight: 700; }}
    header .updated {{ font-size: 12px; opacity: 0.8; }}

    /* ── 사이드바 + 콘텐츠 레이아웃 ── */
    .layout {{
      display: flex;
      min-height: calc(100vh - 56px);
    }}

    nav {{
      width: 220px;
      flex-shrink: 0;
      background: white;
      border-right: var(--border);
      padding: 16px 0;
    }}
    nav .section-title {{
      font-size: 11px;
      font-weight: 600;
      color: #6b7280;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      padding: 8px 16px 4px;
    }}
    nav a {{
      display: block;
      padding: 8px 16px;
      color: var(--gray-700);
      text-decoration: none;
      border-radius: 4px;
      margin: 2px 8px;
      font-size: 13px;
    }}
    nav a:hover, nav a.active {{
      background: var(--primary-light);
      color: var(--primary);
      font-weight: 600;
    }}

    main {{
      flex: 1;
      padding: 24px;
      overflow-x: auto;
    }}

    /* ── 카드 ── */
    .card {{
      background: white;
      border: var(--border);
      border-radius: 8px;
      padding: 20px;
      margin-bottom: 20px;
    }}
    .card-title {{
      font-size: 15px;
      font-weight: 700;
      margin-bottom: 16px;
      color: var(--gray-900);
      border-bottom: var(--border);
      padding-bottom: 12px;
    }}

    /* ── KPI 뱃지 ── */
    .kpi-row {{
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin-bottom: 20px;
    }}
    .kpi {{
      background: white;
      border: var(--border);
      border-radius: 8px;
      padding: 16px 20px;
      flex: 1;
      min-width: 160px;
    }}
    .kpi .label {{ font-size: 12px; color: #6b7280; margin-bottom: 4px; }}
    .kpi .value {{ font-size: 24px; font-weight: 700; }}
    .kpi.primary .value {{ color: var(--primary); }}
    .kpi.success .value {{ color: var(--success); }}
    .kpi.warning .value {{ color: var(--warning); }}
    .kpi.danger .value {{ color: var(--danger); }}

    /* ── 마크다운 렌더링 영역 ── */
    .md-content h1 {{ font-size: 20px; font-weight: 700; margin-bottom: 16px; }}
    .md-content h2 {{ font-size: 15px; font-weight: 700; margin: 20px 0 12px; color: var(--gray-900); }}
    .md-content table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
      margin-bottom: 16px;
    }}
    .md-content th {{
      background: var(--gray-100);
      padding: 8px 12px;
      text-align: left;
      border: var(--border);
      font-weight: 600;
    }}
    .md-content td {{
      padding: 7px 12px;
      border: var(--border);
    }}
    .md-content tr:hover td {{ background: var(--primary-light); }}
    .md-content blockquote {{
      border-left: 4px solid var(--primary);
      padding: 8px 16px;
      background: var(--primary-light);
      border-radius: 0 4px 4px 0;
      margin-bottom: 16px;
      font-size: 13px;
    }}
    .md-content hr {{ border: none; border-top: var(--border); margin: 20px 0; }}
    .md-content p {{ margin-bottom: 8px; line-height: 1.6; }}

    /* ── 탭 ── */
    .tabs {{ display: flex; gap: 0; border-bottom: var(--border); margin-bottom: 16px; }}
    .tab {{
      padding: 8px 16px;
      cursor: pointer;
      border-bottom: 2px solid transparent;
      font-size: 13px;
      color: #6b7280;
    }}
    .tab.active {{ border-bottom-color: var(--primary); color: var(--primary); font-weight: 600; }}

    .tab-panel {{ display: none; }}
    .tab-panel.active {{ display: block; }}

    /* ── 상태 배지 ── */
    .badge {{
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 600;
    }}
    .badge-green {{ background: #d1fae5; color: var(--success); }}
    .badge-red {{ background: #fee2e2; color: var(--danger); }}
    .badge-yellow {{ background: #fef3c7; color: var(--warning); }}
  </style>
</head>
<body>

<header>
  <h1>MA AI 대쉬보드 — 약가 모니터링</h1>
  <span class="updated">마지막 업데이트: {last_updated}</span>
</header>

<div class="layout">
  <nav>
    <div class="section-title">국내 약가</div>
    <a href="#" class="active" onclick="showPanel('domestic', this)">약가 변동 현황</a>
    <a href="#" onclick="showPanel('domestic-history', this)">변동 이력</a>

    <div class="section-title" style="margin-top:16px;">해외 약가</div>
    <a href="#" onclick="showPanel('foreign', this)">해외 약가 조회</a>

    <div class="section-title" style="margin-top:16px;">시스템</div>
    <a href="#" onclick="showPanel('system', this)">에이전트 상태</a>
  </nav>

  <main>

    <!-- 국내 약가 패널 -->
    <div id="panel-domestic" class="tab-panel active">
      <div class="kpi-row">
        <div class="kpi primary">
          <div class="label">전체 급여 약제</div>
          <div class="value">{total_drugs}</div>
        </div>
        <div class="kpi success">
          <div class="label">신규 등재</div>
          <div class="value">{added_count}</div>
        </div>
        <div class="kpi danger">
          <div class="label">삭제</div>
          <div class="value">{deleted_count}</div>
        </div>
        <div class="kpi warning">
          <div class="label">상한금액 변동</div>
          <div class="value">{changed_count}</div>
        </div>
      </div>

      <div class="card">
        <div class="card-title">약가 변동 상세 ({apply_date} 기준)</div>
        <div class="md-content" id="domestic-md-content">로딩 중...</div>
      </div>
    </div>

    <!-- 변동 이력 패널 -->
    <div id="panel-domestic-history" class="tab-panel">
      <div class="card">
        <div class="card-title">약가 변동 이력</div>
        <div class="md-content" id="history-content">로딩 중...</div>
      </div>
    </div>

    <!-- 해외 약가 패널 -->
    <div id="panel-foreign" class="tab-panel">
      <div class="card">
        <div class="card-title">해외 약가 조회</div>
        <div class="md-content" id="foreign-content">
          <blockquote>해외 약가 에이전트가 준비 중입니다.</blockquote>
        </div>
      </div>
    </div>

    <!-- 에이전트 상태 패널 -->
    <div id="panel-system" class="tab-panel">
      <div class="card">
        <div class="card-title">에이전트 실행 상태</div>
        <div class="md-content" id="system-content">{agent_status_html}</div>
      </div>
    </div>

  </main>
</div>

<script>
  // ── 마크다운 렌더링 ──
  const domesticMd = {domestic_md_json};
  const historyMd  = {history_md_json};

  if (domesticMd) {{
    document.getElementById('domestic-md-content').innerHTML = marked.parse(domesticMd);
  }}
  if (historyMd) {{
    document.getElementById('history-content').innerHTML = marked.parse(historyMd);
  }}

  // ── 패널 전환 ──
  function showPanel(name, el) {{
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('nav a').forEach(a => a.classList.remove('active'));
    document.getElementById('panel-' + name).classList.add('active');
    el.classList.add('active');
    return false;
  }}
</script>
</body>
</html>
"""


class DashboardAgent:
    def __init__(self, config: dict, base_dir: Path):
        self.config = config["dashboard"]
        self.domestic_config = config["domestic_agent"]
        self.base_dir = base_dir

        self.dashboard_dir = base_dir / self.config["output_dir"]
        self.processed_dir = base_dir / self.domestic_config["processed_dir"]

        self.domestic_md_path = self.dashboard_dir / self.config["domestic_file"]
        self.output_html_path = self.dashboard_dir / "index.html"
        self.meta_file = self.processed_dir / "last_run_meta.json"

    def _load_meta(self) -> dict:
        if self.meta_file.exists():
            with open(self.meta_file, encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _load_md(self, path: Path) -> str:
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def _build_agent_status_html(self, meta: dict) -> str:
        if not meta:
            return "<p>실행 이력 없음</p>"

        last_run = meta.get("last_run", "-")
        apply_date = meta.get("apply_date", "-")
        changes = meta.get("changes", {})

        return f"""
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
          <tr style="background:#f3f4f6;">
            <th style="padding:8px 12px;border:1px solid #e5e7eb;text-align:left;">에이전트</th>
            <th style="padding:8px 12px;border:1px solid #e5e7eb;text-align:left;">마지막 실행</th>
            <th style="padding:8px 12px;border:1px solid #e5e7eb;text-align:left;">기준일</th>
            <th style="padding:8px 12px;border:1px solid #e5e7eb;text-align:left;">결과</th>
            <th style="padding:8px 12px;border:1px solid #e5e7eb;text-align:left;">상태</th>
          </tr>
          <tr>
            <td style="padding:8px 12px;border:1px solid #e5e7eb;">국내 약가 모니터링</td>
            <td style="padding:8px 12px;border:1px solid #e5e7eb;">{last_run}</td>
            <td style="padding:8px 12px;border:1px solid #e5e7eb;">{apply_date}</td>
            <td style="padding:8px 12px;border:1px solid #e5e7eb;">
              신규 {changes.get('added', 0)} / 삭제 {changes.get('deleted', 0)} / 변동 {changes.get('changed', 0)}
            </td>
            <td style="padding:8px 12px;border:1px solid #e5e7eb;">
              <span style="background:#d1fae5;color:#057a55;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:600;">완료</span>
            </td>
          </tr>
          <tr>
            <td style="padding:8px 12px;border:1px solid #e5e7eb;">해외 약가 조회</td>
            <td style="padding:8px 12px;border:1px solid #e5e7eb;">-</td>
            <td style="padding:8px 12px;border:1px solid #e5e7eb;">-</td>
            <td style="padding:8px 12px;border:1px solid #e5e7eb;">-</td>
            <td style="padding:8px 12px;border:1px solid #e5e7eb;">
              <span style="background:#fef3c7;color:#c27803;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:600;">준비 중</span>
            </td>
          </tr>
        </table>
        """

    def run(self):
        """대쉬보드 HTML을 생성한다."""
        logger.info("=== 대쉬보드 에이전트 시작 ===")

        meta = self._load_meta()
        domestic_md = self._load_md(self.domestic_md_path)

        changes = meta.get("changes", {})
        apply_date = meta.get("apply_date", datetime.today().strftime("%Y.%m.%d"))
        last_updated = meta.get("last_run", datetime.now().isoformat())

        # 이력 마크다운 생성 (change_history.json → Markdown)
        history_md = self._build_history_markdown()

        agent_status_html = self._build_agent_status_html(meta)

        html = HTML_TEMPLATE.format(
            last_updated=last_updated,
            total_drugs=f"{meta.get('total_drugs', 0):,}",
            added_count=changes.get("added", 0),
            deleted_count=changes.get("deleted", 0),
            changed_count=changes.get("changed", 0),
            apply_date=apply_date,
            domestic_md_json=json.dumps(domestic_md, ensure_ascii=False),
            history_md_json=json.dumps(history_md, ensure_ascii=False),
            agent_status_html=agent_status_html,
        )

        self.output_html_path.write_text(html, encoding="utf-8")
        logger.info("대쉬보드 HTML 생성 완료: %s", self.output_html_path)
        return self.output_html_path

    def _build_history_markdown(self) -> str:
        history_file = self.processed_dir / "change_history.json"
        if not history_file.exists():
            return "변동 이력 데이터가 없습니다."

        with open(history_file, encoding="utf-8") as f:
            history = json.load(f)

        lines = [
            "# 약가 변동 이력",
            "",
            "| 적용일 | 신규 등재 | 삭제 | 가격변동 | 실행 시각 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for entry in reversed(history):
            s = entry["요약"]
            lines.append(
                f"| {entry['적용일']} | {s['신규등재']}개 | {s['삭제']}개 | {s['가격변동']}개 | {entry['실행시각']} |"
            )

        lines += ["", "---", "", "## 상세 변동 내역 (최근 3개월)", ""]

        for entry in reversed(history[-3:]):
            lines.append(f"### {entry['적용일']}")
            lines.append("")

            for item in entry["상세"].get("가격변동", [])[:20]:
                lines.append(f"- **{item['제품명']}** ({item['보험코드']}): "
                             f"{item['이전상한금액']}원 → {item['신규상한금액']}원 ({item.get('변동률', 'N/A')})")

            if not entry["상세"].get("가격변동"):
                lines.append("> 가격 변동 없음")
            lines.append("")

        return "\n".join(lines)


def load_config(config_path: Path) -> dict:
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    base_dir = Path(__file__).parent.parent
    config = load_config(base_dir / "config" / "settings.json")
    agent = DashboardAgent(config, base_dir)
    html_path = agent.run()
    print(f"대쉬보드: file://{html_path.resolve()}")
