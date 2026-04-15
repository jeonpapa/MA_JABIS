"""
국내 약가 모니터링 에이전트
- HIRA(건강보험심사평가원) 약가고시현황 게시판 모니터링
- 최신 게시물의 첨부 Excel 파일을 data/raw/ 에 다운로드
- SQLite DB에 적재 및 변동 내역(신규/삭제/가격변동) 추적
- 대쉬보드 소스 파일(Markdown) 자동 생성
"""

import asyncio
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from playwright.async_api import async_playwright

from agents.db import DrugPriceDB

logger = logging.getLogger(__name__)


class DomesticPriceAgent:
    def __init__(self, config: dict, base_dir: Path):
        self.config = config["domestic_agent"]
        self.dashboard_config = config["dashboard"]
        self.base_dir = base_dir

        self.raw_dir = base_dir / "data" / "raw"
        self.db_path = base_dir / "data" / "db" / "drug_prices.db"
        self.dashboard_dir = base_dir / self.dashboard_config["output_dir"]
        self.processed_dir = base_dir / self.config["processed_dir"]

        self.hira_url = self.config["hira_board_url"]
        self.db = DrugPriceDB(self.db_path)
        self.meta_file = self.processed_dir / "last_run_meta.json"

    # ──────────────────────────────────────────────────────────────────────
    # 1. HIRA 최신 게시물 Excel 다운로드
    # ──────────────────────────────────────────────────────────────────────

    async def download_latest_excel(self) -> tuple:
        """HIRA 게시판에서 최신 게시물의 첨부 Excel 파일을 data/raw/ 에 다운로드.
        (file_path, apply_date) 반환."""
        logger.info("HIRA 게시판 접속 중: %s", self.hira_url)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                accept_downloads=True,
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()

            try:
                await page.goto(self.hira_url, wait_until="networkidle", timeout=30000)

                # 첫 번째(최신) 게시물 클릭
                first_row = page.locator("table tbody tr").first
                post_link = first_row.locator("a").first
                post_title = (await post_link.inner_text()).strip()
                logger.info("최신 게시물: %s", post_title)

                # brdBltNo 추출
                href = await post_link.get_attribute("href")
                brd_match = re.search(r"brdBltNo=(\d+)", href or "")
                brd_blt_no = int(brd_match.group(1)) if brd_match else None

                # 이미 처리된 게시물이면 스킵
                if brd_blt_no and self.db.is_processed(brd_blt_no):
                    logger.info("이미 처리된 최신 게시물 (brdBltNo=%d). 업데이트 없음.", brd_blt_no)
                    await browser.close()
                    return None, ""

                await post_link.click()
                await page.wait_for_load_state("networkidle", timeout=15000)

                # 기준일 추출 (제목/본문에서)
                try:
                    title_el = page.locator(".bbs_view_title, h3.tit, .view_tit").first
                    title_text = await title_el.inner_text()
                except Exception:
                    title_text = post_title

                date_match = re.search(r"(\d{4})[.\-](\d{1,2})[.\-](\d{1,2})", title_text)
                apply_date = (
                    f"{date_match.group(1)}.{date_match.group(2).zfill(2)}.{date_match.group(3).zfill(2)}"
                    if date_match else datetime.today().strftime("%Y.%m.%d")
                )

                # ── 파일 다운로드 링크: a.btn_file (SNS 공유 버튼 제외한 정확한 선택자)
                file_links = page.locator("a.btn_file")
                btn_count = await file_links.count()

                if btn_count == 0:
                    logger.error("첨부파일(btn_file) 링크를 찾을 수 없습니다.")
                    await browser.close()
                    return None, apply_date

                # 여러 파일이 있으면 Excel 우선 선택
                excel_link = file_links.first
                for i in range(btn_count):
                    onclick = await file_links.nth(i).get_attribute("onclick") or ""
                    if "xls" in onclick.lower():
                        excel_link = file_links.nth(i)
                        break

                # 다운로드
                async with page.expect_download(timeout=60000) as dl_info:
                    await excel_link.click()
                download = await dl_info.value
                suggested = download.suggested_filename

                clean_date = apply_date.replace(".", "")
                safe_name = re.sub(r"[^\w\-.]", "_", suggested)
                save_name = f"{clean_date}_{safe_name}"
                save_path = self.raw_dir / save_name

                await download.save_as(str(save_path))
                logger.info("다운로드 완료: %s", save_path.name)

                # 다운로드 로그
                if brd_blt_no:
                    self.db.log_download(
                        brd_blt_no=brd_blt_no,
                        post_number=0,
                        apply_date=apply_date,
                        filename=save_path.name,
                        file_path=str(save_path),
                        status="success",
                    )
                return save_path, apply_date

            except Exception as e:
                logger.error("다운로드 실패: %s", e, exc_info=True)
                return None, ""
            finally:
                await browser.close()

    # ──────────────────────────────────────────────────────────────────────
    # 2. Excel 파싱
    # ──────────────────────────────────────────────────────────────────────

    def parse_excel(self, excel_path: Path) -> pd.DataFrame:
        xl = pd.ExcelFile(excel_path)
        for sheet in xl.sheet_names:
            for skip in range(6):
                try:
                    temp = pd.read_excel(excel_path, sheet_name=sheet, header=skip, dtype=str)
                    temp.columns = [str(c).strip() for c in temp.columns]
                    if any(
                        any(k in col for k in ["보험코드", "급여코드"])
                        for col in temp.columns
                    ):
                        df = temp.dropna(how="all").fillna("")
                        logger.info("Excel 파싱 완료: 시트='%s', %d행", sheet, len(df))
                        return df
                except Exception:
                    pass
        logger.error("유효한 데이터 시트 없음: %s", excel_path.name)
        return pd.DataFrame()

    # ──────────────────────────────────────────────────────────────────────
    # 3. 변동 비교 (DB 기준 직전 월 vs 현재)
    # ──────────────────────────────────────────────────────────────────────

    def compute_changes(self, df: pd.DataFrame, apply_date: str) -> dict:
        """현재 DataFrame과 DB의 직전 적용일 데이터를 비교해 변동 내역을 반환."""
        col_map = self.db.map_columns(list(df.columns))
        code_col = col_map.get("insurance_code", "보험코드")
        price_col = col_map.get("max_price", "상한금액")
        name_col = col_map.get("product_name_kr", "한글제품명")
        company_col = col_map.get("company", "업체명")
        ingredient_col = col_map.get("ingredient", "성분명(일반명)")

        # 현재 데이터 맵
        new_map = {}
        for _, row in df.iterrows():
            code = str(row.get(code_col, "")).strip()
            if code and code not in ("nan", "보험코드"):
                new_map[code] = row.to_dict()

        # DB에서 직전 적용일 데이터 로드
        dates = self.db.get_available_dates()
        dates = [d for d in dates if d < apply_date]
        if not dates:
            logger.info("이전 데이터 없음 — 최초 적재")
            return {"신규등재": [], "삭제": [], "가격변동": []}

        prev_date = dates[-1]
        with self.db._connect() as conn:
            rows = conn.execute(
                "SELECT insurance_code, product_name_kr, company, ingredient, max_price "
                "FROM drug_prices WHERE apply_date=?",
                (prev_date,)
            ).fetchall()
        old_map = {r["insurance_code"]: dict(r) for r in rows}
        logger.info("비교 기준: %s (%d개) → %s (%d개)",
                    prev_date, len(old_map), apply_date, len(new_map))

        new_codes = set(new_map.keys())
        old_codes = set(old_map.keys())

        added, deleted, changed = [], [], []

        for code in new_codes - old_codes:
            r = new_map[code]
            added.append({
                "보험코드": code,
                "제품명": r.get(name_col, ""),
                "업체명": r.get(company_col, ""),
                "성분명": r.get(ingredient_col, ""),
                "신규상한금액": r.get(price_col, ""),
                "이전상한금액": "-",
                "변동유형": "신규등재",
                "적용일": apply_date,
            })

        for code in old_codes - new_codes:
            r = old_map[code]
            deleted.append({
                "보험코드": code,
                "제품명": r.get("product_name_kr", ""),
                "업체명": r.get("company", ""),
                "성분명": r.get("ingredient", ""),
                "신규상한금액": "-",
                "이전상한금액": str(r.get("max_price", "")),
                "변동유형": "삭제",
                "적용일": apply_date,
            })

        for code in new_codes & old_codes:
            new_price_raw = str(new_map[code].get(price_col, "")).replace(",", "").strip()
            old_price_raw = str(old_map[code].get("max_price", "")).strip()
            try:
                np_val = int(float(new_price_raw)) if new_price_raw else None
                op_val = int(old_price_raw) if old_price_raw and old_price_raw != "None" else None
            except ValueError:
                np_val = op_val = None

            if np_val is not None and op_val is not None and np_val != op_val:
                try:
                    rate = round((np_val - op_val) / op_val * 100, 2)
                    rate_str = f"{rate:+.2f}%"
                except ZeroDivisionError:
                    rate_str = "N/A"
                r = new_map[code]
                changed.append({
                    "보험코드": code,
                    "제품명": r.get(name_col, ""),
                    "업체명": r.get(company_col, ""),
                    "성분명": r.get(ingredient_col, ""),
                    "신규상한금액": f"{np_val:,}",
                    "이전상한금액": f"{op_val:,}",
                    "변동률": rate_str,
                    "변동유형": "가격변동",
                    "적용일": apply_date,
                })

        logger.info("변동: 신규 %d / 삭제 %d / 가격변동 %d",
                    len(added), len(deleted), len(changed))
        return {"신규등재": added, "삭제": deleted, "가격변동": changed}

    # ──────────────────────────────────────────────────────────────────────
    # 4. 대쉬보드 Markdown 생성
    # ──────────────────────────────────────────────────────────────────────

    def generate_dashboard_markdown(self, changes: dict, apply_date: str, total_count: int):
        output_path = self.dashboard_dir / self.dashboard_config["domestic_file"]
        added = changes["신규등재"]
        deleted = changes["삭제"]
        changed = changes["가격변동"]
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        lines = [
            "# 국내 약가 현황 대쉬보드",
            "",
            f"> **기준일:** {apply_date} | **마지막 업데이트:** {now_str}",
            "",
            "---",
            "",
            "## 요약",
            "",
            "| 항목 | 수치 |",
            "| --- | --- |",
            f"| 전체 급여 약제 수 | **{total_count:,}개** |",
            f"| 신규 등재 | **{len(added)}개** |",
            f"| 삭제 | **{len(deleted)}개** |",
            f"| 상한금액 변동 | **{len(changed)}개** |",
            "",
            "---",
            "",
        ]

        if changed:
            lines += [
                f"## 상한금액 변동 ({apply_date})",
                "",
                "| 보험코드 | 제품명 | 업체명 | 성분명 | 이전(원) | 신규(원) | 변동률 |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
            for item in sorted(changed, key=lambda x: x.get("변동률", ""), reverse=True):
                lines.append(
                    f"| {item['보험코드']} | {item['제품명']} | {item['업체명']} | "
                    f"{item['성분명']} | {item['이전상한금액']} | {item['신규상한금액']} | {item.get('변동률', 'N/A')} |"
                )
            lines += ["", "---", ""]

        if added:
            lines += [
                f"## 신규 등재 ({apply_date})",
                "",
                "| 보험코드 | 제품명 | 업체명 | 성분명 | 상한금액(원) |",
                "| --- | --- | --- | --- | --- |",
            ]
            for item in added:
                lines.append(
                    f"| {item['보험코드']} | {item['제품명']} | {item['업체명']} | "
                    f"{item['성분명']} | {item['신규상한금액']} |"
                )
            lines += ["", "---", ""]

        if deleted:
            lines += [
                f"## 삭제 ({apply_date})",
                "",
                "| 보험코드 | 제품명 | 업체명 | 성분명 | 이전상한금액(원) |",
                "| --- | --- | --- | --- | --- |",
            ]
            for item in deleted:
                lines.append(
                    f"| {item['보험코드']} | {item['제품명']} | {item['업체명']} | "
                    f"{item['성분명']} | {item['이전상한금액']} |"
                )
            lines += ["", "---", ""]

        if not added and not deleted and not changed:
            lines += [
                f"## {apply_date} 변동 내역",
                "",
                "> 이번 업데이트에서 변동된 약제가 없습니다.",
                "",
            ]

        # DB 이력 요약
        stats = self.db.get_stats()
        lines += [
            "## 데이터베이스 현황",
            "",
            "| 항목 | 값 |",
            "| --- | --- |",
            f"| 누적 레코드 수 | {stats['total_records']:,}건 |",
            f"| 수록 기간 | {stats['oldest_date']} ~ {stats['latest_date']} |",
            f"| 월별 데이터 수 | {stats['total_dates']}개월 |",
            "",
        ]

        output_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("대쉬보드 Markdown 생성: %s", output_path)
        return output_path

    # ──────────────────────────────────────────────────────────────────────
    # 5. 메인 실행
    # ──────────────────────────────────────────────────────────────────────

    async def run(self):
        logger.info("=== 국내 약가 에이전트 시작 ===")

        excel_path, apply_date = await self.download_latest_excel()
        if excel_path is None:
            if apply_date == "":
                logger.error("다운로드 실패. 종료.")
            else:
                logger.info("업데이트 없음.")
            return None

        df = self.parse_excel(excel_path)
        if df.empty:
            logger.error("Excel 파싱 실패. 종료.")
            return None

        changes = self.compute_changes(df, apply_date)

        # DB 적재
        count = self.db.upsert_prices(df, apply_date)

        self.generate_dashboard_markdown(changes, apply_date, len(df))

        meta = {
            "last_run": datetime.now().isoformat(),
            "apply_date": apply_date,
            "excel_file": str(excel_path),
            "total_drugs": len(df),
            "db_records": count,
            "changes": {
                "added": len(changes["신규등재"]),
                "deleted": len(changes["삭제"]),
                "changed": len(changes["가격변동"]),
            },
        }
        self.meta_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.meta_file, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        logger.info("=== 완료: 전체 %d개, 신규 %d, 삭제 %d, 변동 %d ===",
                    len(df), len(changes["신규등재"]),
                    len(changes["삭제"]), len(changes["가격변동"]))
        return meta


def load_config(config_path: Path) -> dict:
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    base_dir = Path(__file__).parent.parent
    config = load_config(base_dir / "config" / "settings.json")
    agent = DomesticPriceAgent(config, base_dir)
    asyncio.run(agent.run())
