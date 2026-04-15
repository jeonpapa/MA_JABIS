"""
HIRA 약가 히스토리 백필 에이전트
- HIRA 약가고시현황 게시판의 전체 게시물(226건)을 순회
- 각 게시물의 첨부 Excel 파일을 data/raw/ 에 다운로드
- 다운로드된 파일을 파싱해 SQLite DB에 적재
- 이미 처리된 파일은 건너뜀 (재실행 안전)
- 사용자가 직접 놓은 파일도 data/raw/ 에 넣으면 자동 처리
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

HIRA_BOARD_URL = "https://www.hira.or.kr/bbsDummyKR.do?pgmid=HIRAA030014050000"
PAGE_SIZE = 10   # HIRA 기본 페이지당 게시물 수
DELAY_SEC = 2    # 요청 사이 대기 시간 (서버 부하 방지)


class BackfillAgent:
    def __init__(self, config: dict, base_dir: Path):
        self.base_dir = base_dir
        self.raw_dir = base_dir / "data" / "raw"
        self.db_path = base_dir / "data" / "db" / "drug_prices.db"
        self.col_config = config["domestic_agent"]["excel"]["columns"]
        self.db = DrugPriceDB(self.db_path)

    # ──────────────────────────────────────────────────────────────────────
    # 1. 게시판 전체 게시물 목록 수집
    # ──────────────────────────────────────────────────────────────────────

    async def collect_all_posts(self) -> list:
        """게시판 전체 페이지를 순회해 게시물 정보 목록 반환.
        페이지네이션은 총 건수를 파싱하지 않고, 빈 페이지가 나올 때까지 순회."""
        logger.info("HIRA 게시판 전체 게시물 목록 수집 시작")
        posts = []

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            )
            page = await context.new_page()

            page_idx = 1
            consecutive_empty = 0

            while True:
                url = f"{HIRA_BOARD_URL}&pageIndex={page_idx}"
                try:
                    await page.goto(url, wait_until="networkidle", timeout=30000)
                except Exception as e:
                    logger.warning("페이지 %d 로딩 실패: %s", page_idx, e)
                    page_idx += 1
                    if consecutive_empty >= 2:
                        break
                    continue

                # 페이지 내 brdBltNo 를 포함한 링크를 모두 추출
                page_content = await page.content()
                found_hrefs = re.findall(
                    r'href="([^"]*brdBltNo=(\d+)[^"]*)"', page_content
                )

                if not found_hrefs:
                    consecutive_empty += 1
                    logger.info("페이지 %d: 게시물 없음 (연속 빈 페이지: %d)",
                                page_idx, consecutive_empty)
                    if consecutive_empty >= 2:
                        break
                    page_idx += 1
                    await asyncio.sleep(1)
                    continue

                consecutive_empty = 0
                page_posts = []
                seen_brd = set()

                for href, brd_str in found_hrefs:
                    brd_blt_no = int(brd_str)
                    if brd_blt_no in seen_brd:
                        continue
                    seen_brd.add(brd_blt_no)

                    # 번호 및 날짜 추출 (href 주변 컨텍스트에서)
                    num_match = re.search(r"pageIndex2=(\d+)", href)
                    post_num = int(num_match.group(1)) if num_match else 0

                    page_posts.append({
                        "post_number": post_num,
                        "brd_blt_no": brd_blt_no,
                        "posted_date": "",
                        "href": href if href.startswith("?") else f"?{href.split('?')[-1]}",
                    })

                # 게시물 번호는 목록 순서(위쪽이 최신)로 보정
                rows = page.locator("table tbody tr")
                row_count = await rows.count()
                for i in range(row_count):
                    try:
                        row = rows.nth(i)
                        num_text = (await row.locator("td:first-child").inner_text()).strip()
                        link = row.locator("a").first
                        href_attr = await link.get_attribute("href") or ""
                        brd_match = re.search(r"brdBltNo=(\d+)", href_attr)
                        date_cells = row.locator("td")
                        date_text = ""
                        dc = await date_cells.count()
                        if dc >= 4:
                            date_text = (await date_cells.nth(3).inner_text()).strip()

                        if brd_match:
                            brd = int(brd_match.group(1))
                            for p in page_posts:
                                if p["brd_blt_no"] == brd:
                                    try:
                                        p["post_number"] = int(num_text)
                                    except ValueError:
                                        pass
                                    p["posted_date"] = date_text
                                    p["href"] = href_attr
                                    break
                    except Exception:
                        pass

                new_posts = [p for p in page_posts
                             if not any(ep["brd_blt_no"] == p["brd_blt_no"] for ep in posts)]
                posts.extend(new_posts)
                logger.info("페이지 %d 수집 완료: %d건 추가 (총 %d건)",
                            page_idx, len(new_posts), len(posts))

                page_idx += 1
                await asyncio.sleep(1)

            await browser.close()

        result = sorted(posts, key=lambda x: x["post_number"])
        logger.info("전체 게시물 목록 수집 완료: %d건", len(result))
        return result

    # ──────────────────────────────────────────────────────────────────────
    # 2. 단일 게시물에서 Excel 다운로드
    # ──────────────────────────────────────────────────────────────────────

    async def download_post_excel(
        self, page, post: dict
    ) -> tuple:
        """게시물 상세 페이지에서 Excel 첨부파일을 다운로드. (path, apply_date) 반환"""
        base_url = "https://www.hira.or.kr"
        detail_url = f"{base_url}{post['href']}" if post["href"].startswith("/") else post["href"]
        if "hira.or.kr" not in detail_url:
            detail_url = f"{base_url}/bbsDummyKR.do{post['href']}"

        try:
            await page.goto(detail_url, wait_until="networkidle", timeout=20000)

            # 기준일 추출 — 페이지 본문 전체 텍스트에서 날짜 패턴 탐색
            page_text = await page.inner_text("body")
            date_match = re.search(r"(\d{4})[.\-](\d{1,2})[.\-](\d{1,2})", page_text)
            apply_date = (
                f"{date_match.group(1)}.{date_match.group(2).zfill(2)}.{date_match.group(3).zfill(2)}"
                if date_match else datetime.today().strftime("%Y.%m.%d")
            )

            # ── 파일 다운로드 링크: a.btn_file (SNS 공유 버튼 제외한 정확한 선택자)
            file_links = page.locator("a.btn_file")
            btn_count = await file_links.count()

            if btn_count == 0:
                # 구형 페이지 대비 폴백: utilshare-list 외부의 a[href='#none']
                file_links = page.locator("a[href='#none']").filter(
                    has_not=page.locator(".utilshare-list")
                )
                btn_count = await file_links.count()

            if btn_count == 0:
                logger.warning("게시물 %d: 첨부파일 없음", post["post_number"])
                return None, apply_date

            # 여러 파일이 있으면 각각 시도해 Excel 파일 선택
            excel_link = file_links.first  # 기본값
            for i in range(btn_count):
                onclick = await file_links.nth(i).get_attribute("onclick") or ""
                # onclick 파라미터에 xlsx/xls 힌트가 있으면 우선 선택
                if "xls" in onclick.lower():
                    excel_link = file_links.nth(i)
                    break

            # 다운로드
            ALLOWED_EXTENSIONS = {".xls", ".xlsx", ".zip"}

            async with page.expect_download(timeout=60000) as dl_info:
                await excel_link.click()
            download = await dl_info.value
            suggested = download.suggested_filename
            ext = Path(suggested).suffix.lower()

            # Excel / ZIP 이외 파일(PDF, HWP 등)은 저장하지 않고 건너뜀
            if ext not in ALLOWED_EXTENSIONS:
                logger.warning(
                    "게시물 %d: 지원하지 않는 파일 형식 '%s' → 건너뜀 (%s)",
                    post["post_number"], ext, suggested,
                )
                return None, apply_date

            # raw/ 폴더에 저장 (기준일 + 게시물번호로 정규화된 파일명)
            clean_date = apply_date.replace(".", "")
            safe_name = re.sub(r"[^\w\-.]", "_", suggested)
            save_name = f"{clean_date}_#{post['post_number']}_{safe_name}"
            save_path = self.raw_dir / save_name

            await download.save_as(str(save_path))
            logger.info("다운로드: %s", save_path.name)
            return save_path, apply_date

        except Exception as e:
            logger.error("게시물 %d 다운로드 실패: %s", post["post_number"], e)
            return None, ""

    # ──────────────────────────────────────────────────────────────────────
    # 3. Excel 파싱 및 DB 적재
    # ──────────────────────────────────────────────────────────────────────

    def _find_excel_in_zip(self, zip_path: Path) -> Path:
        """ZIP 파일에서 Excel 파일을 추출해 임시 경로에 저장 후 반환."""
        import zipfile, tempfile
        extract_dir = zip_path.parent / f"_unzip_{zip_path.stem}"
        extract_dir.mkdir(exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            # 인코딩 문제 대비 (한글 파일명)
            for info in zf.infolist():
                try:
                    fname = info.filename.encode("cp437").decode("euc-kr")
                except Exception:
                    fname = info.filename
                target = extract_dir / Path(fname).name
                target.write_bytes(zf.read(info.filename))
                if target.suffix.lower() in (".xls", ".xlsx"):
                    logger.info("ZIP 내 Excel 발견: %s", target.name)
                    return target
        # 못 찾으면 None 반환
        return None

    def process_excel(self, file_path: Path, apply_date: str) -> int:
        """Excel(또는 ZIP 포함 Excel) 파일을 파싱해 DB에 삽입. 저장된 레코드 수 반환."""
        logger.info("처리 중: %s (기준일: %s)", file_path.name, apply_date)
        try:
            # ZIP 파일이면 내부 Excel 추출
            actual_path = file_path
            if file_path.suffix.lower() == ".zip":
                extracted = self._find_excel_in_zip(file_path)
                if extracted is None:
                    logger.warning("ZIP 내 Excel 파일 없음: %s", file_path.name)
                    return 0
                actual_path = extracted

            # .xls / .xlsx 파싱
            engine = "xlrd" if actual_path.suffix.lower() == ".xls" else None
            xl = pd.ExcelFile(actual_path, engine=engine)
            df = None
            for sheet in xl.sheet_names:
                for skip in range(6):
                    try:
                        temp = pd.read_excel(
                            actual_path, sheet_name=sheet,
                            header=skip, dtype=str, engine=engine
                        )
                        temp.columns = [str(c).strip() for c in temp.columns]
                        cols = temp.columns.tolist()

                        # 현행 포맷 감지: 보험코드 컬럼 존재
                        is_modern = any(
                            any(cand in col for cand in ["보험코드", "보험\n코드", "급여코드"])
                            for col in cols
                        )
                        # 구형 포맷 감지: 제품코드 + 상한금액 컬럼 존재
                        is_legacy = "제품코드" in cols and "상한금액" in cols

                        if is_modern:
                            df = temp
                            break
                        elif is_legacy:
                            # 구형 포맷: 성분 헤더행(제품명이 NaN인 행) 제거
                            # 제품명이 있는 행만 남김
                            temp = temp[temp["제품명"].notna() & (temp["제품명"].str.strip() != "")]
                            df = temp.copy()
                            logger.info("구형 포맷 감지: %d개 약제 (헤더행 제거 후)", len(df))
                            break
                    except Exception:
                        pass
                if df is not None:
                    break

            if df is None:
                logger.warning("유효한 데이터 시트 없음: %s", file_path.name)
                return 0

            df = df.dropna(how="all").fillna("")
            count = self.db.upsert_prices(df, apply_date)
            return count

        except Exception as e:
            logger.error("Excel 처리 오류 (%s): %s", file_path.name, e, exc_info=True)
            return 0

    # ──────────────────────────────────────────────────────────────────────
    # 4. 사용자가 직접 넣은 파일 처리 (raw/ 폴더 스캔)
    # ──────────────────────────────────────────────────────────────────────

    def process_existing_files(self):
        """
        data/raw/ 폴더에 이미 있는 Excel 파일을 처리한다.
        - 파일명에서 날짜 추출 시도
        - DB 로그에 없는(미처리) 파일만 처리
        """
        ALLOWED_EXTENSIONS = {".xls", ".xlsx", ".zip"}
        excel_files = sorted(
            f for f in self.raw_dir.iterdir()
            if f.suffix.lower() in ALLOWED_EXTENSIONS
        )
        logger.info("data/raw/ 기존 파일 %d개 발견 (xls/xlsx/zip 한정)", len(excel_files))

        for f in excel_files:
            # 이미 처리된 파일 확인 (파일 경로로 확인)
            with self.db._connect() as conn:
                row = conn.execute(
                    "SELECT process_status FROM download_log WHERE file_path=?",
                    (str(f),)
                ).fetchone()
            if row and row["process_status"] == "success":
                logger.info("이미 처리됨, 건너뜀: %s", f.name)
                continue

            # 파일명에서 날짜 추출
            date_match = re.search(r"(\d{4})(\d{2})(\d{2})", f.name)
            if date_match:
                apply_date = f"{date_match.group(1)}.{date_match.group(2)}.{date_match.group(3)}"
            else:
                date_match2 = re.search(r"(\d{4})[.\-_](\d{1,2})[.\-_](\d{1,2})", f.name)
                if date_match2:
                    apply_date = (
                        f"{date_match2.group(1)}.{date_match2.group(2).zfill(2)}"
                        f".{date_match2.group(3).zfill(2)}"
                    )
                else:
                    apply_date = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y.%m.%d")
                    logger.warning("날짜 추출 실패, 수정일 사용: %s → %s", f.name, apply_date)

            # DB 로그 등록 후 처리
            with self.db._connect() as conn:
                conn.execute("""
                    INSERT OR IGNORE INTO download_log
                    (brd_blt_no, post_number, apply_date, filename, file_path,
                     download_status, downloaded_at)
                    VALUES (NULL, NULL, ?, ?, ?, 'success', ?)
                """, (apply_date, f.name, str(f), datetime.now().isoformat()))

            count = self.process_excel(f, apply_date)
            if count > 0:
                with self.db._connect() as conn:
                    conn.execute("""
                        UPDATE download_log
                        SET process_status='success', record_count=?, processed_at=?
                        WHERE file_path=?
                    """, (count, datetime.now().isoformat(), str(f)))
                logger.info("처리 완료: %s → %d건", f.name, count)
            else:
                with self.db._connect() as conn:
                    conn.execute("""
                        UPDATE download_log
                        SET process_status='failed', processed_at=?
                        WHERE file_path=?
                    """, (datetime.now().isoformat(), str(f)))

    # ──────────────────────────────────────────────────────────────────────
    # 5. 전체 백필 실행 (HIRA 스크래핑 + DB 적재)
    # ──────────────────────────────────────────────────────────────────────

    async def run_full_backfill(self):
        """HIRA에서 전체 게시물을 순회해 다운로드 + DB 적재를 수행한다."""
        logger.info("=== 전체 백필 시작 ===")

        # 0) 이미 raw/에 있는 파일 먼저 처리
        self.process_existing_files()

        # 1) 게시물 목록 수집
        posts = await self.collect_all_posts()
        total = len(posts)
        logger.info("처리 대상: %d건", total)

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

            for idx, post in enumerate(posts, 1):
                brd = post["brd_blt_no"]

                # 이미 다운로드 + 처리됐으면 스킵
                if self.db.is_processed(brd):
                    logger.info("[%d/%d] 스킵 (이미 처리됨): 게시물 #%d",
                                idx, total, post["post_number"])
                    continue

                logger.info("[%d/%d] 처리 중: 게시물 #%d (brdBltNo=%d)",
                            idx, total, post["post_number"], brd)

                # 다운로드
                if not self.db.is_downloaded(brd):
                    save_path, apply_date = await self.download_post_excel(page, post)
                    if save_path:
                        self.db.log_download(
                            brd_blt_no=brd,
                            post_number=post["post_number"],
                            apply_date=apply_date,
                            filename=save_path.name,
                            file_path=str(save_path),
                            status="success",
                        )
                    else:
                        self.db.log_download(
                            brd_blt_no=brd,
                            post_number=post["post_number"],
                            status="failed",
                        )
                        await asyncio.sleep(DELAY_SEC)
                        continue
                else:
                    # 다운로드됐지만 처리 안 된 경우 — 파일 경로 조회
                    with self.db._connect() as conn:
                        row = conn.execute(
                            "SELECT file_path, apply_date FROM download_log WHERE brd_blt_no=?",
                            (brd,)
                        ).fetchone()
                    if row:
                        save_path = Path(row["file_path"])
                        apply_date = row["apply_date"] or ""
                    else:
                        continue

                # DB 적재 (Excel / ZIP 파일만)
                if save_path.suffix.lower() not in {".xls", ".xlsx", ".zip"}:
                    logger.warning("지원하지 않는 파일 형식, 적재 건너뜀: %s", save_path.name)
                    self.db.log_process(brd, "failed", 0, f"지원하지 않는 형식: {save_path.suffix}")
                    await asyncio.sleep(DELAY_SEC)
                    continue
                count = self.process_excel(save_path, apply_date)
                if count > 0:
                    self.db.log_process(brd, "success", count)
                else:
                    self.db.log_process(brd, "failed", 0, "레코드 없음 또는 파싱 실패")

                await asyncio.sleep(DELAY_SEC)

            await browser.close()

        # 최종 통계 출력
        stats = self.db.get_stats()
        logger.info("=== 백필 완료 ===")
        logger.info("  전체 레코드: %s건", f"{stats['total_records']:,}")
        logger.info("  기간: %s ~ %s", stats["oldest_date"], stats["latest_date"])
        logger.info("  다운로드 파일: %s", stats["downloaded_files"])
        return stats


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
    agent = BackfillAgent(config, base_dir)
    asyncio.run(agent.run_full_backfill())
