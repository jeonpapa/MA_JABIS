"""HIRA 약제급여평가위원회 공개 게시판 수집기 — 평가결과 PDF 자동 발견·다운로드.

게시판: https://www.hira.or.kr/bbsDummy.do?pgmid=HIRAA030014040000&brdScnBltNo=4&brdBltNo=<번호>
 - 로그인 불필요. 게시물 번호(brdBltNo)는 증가 순번이라 '마지막으로 아는 번호 + 1' 부터 순차 조회하면 목록 페이지(JS 렌더링)를
   파싱하지 않고도 신규 게시물을 찾는다. 첨부가 없는 번호가 연속 N 개면 끝으로 본다(중간에 결번이 있을 수 있어 N>1).
 - 첨부: onclick="downLoadBbs('<apndNo>','<brdBltNo>','17','58')" → /bbs/bbsCDownLoad.do?apndNo=..&apndBrdBltNo=..&apndBrdTyNo=..&apndBltNo=..
   평가결과 PDF 는 이름이 '평가결과_' 로 시작하는 pdf 첨부(회의자료·hwp 는 받지 않는다).
 - 게시물 제목은 성분·함량(예 'mepolizumab 0.1g(0.1g/mL)'), 날짜는 게시일이다(약평위 심의일 아님).

네트워크 접근은 get 함수로 주입해 테스트에서 실제 호출 없이 검증한다.
"""
from __future__ import annotations

import html as _html
import re
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Optional

BOARD_URL = "https://www.hira.or.kr/bbsDummy.do?pgmid=HIRAA030014040000&brdScnBltNo=4&brdBltNo={blt}&pageIndex=1"
DOWNLOAD_URL = "https://www.hira.or.kr/bbs/bbsCDownLoad.do?apndNo={no}&apndBrdBltNo={blt}&apndBrdTyNo={ty}&apndBltNo={bn}"
_UA = {"User-Agent": "Mozilla/5.0 (compatible; MA-AI-Dossier/1.0)"}

_RE_TITLE = re.compile(r'<div class="viewCont">\s*<div class="title">(.*?)</div>', re.S)
_RE_DATE = re.compile(r'<ul class="writer">.*?(20\d\d-\d\d-\d\d)', re.S)
_RE_FILE = re.compile(
    r'<li>\s*<i class="icon-file-(\w+)"[^>]*></i>\s*(.*?)\s*<a[^>]*onclick="downLoadBbs\(\'(\d+)\',\'(\d+)\',\'(\d+)\',\'(\d+)\'\);"', re.S)


@dataclass
class Attachment:
    ext: str
    name: str
    no: str
    blt: str
    ty: str
    bn: str

    @property
    def url(self) -> str:
        return DOWNLOAD_URL.format(no=self.no, blt=self.blt, ty=self.ty, bn=self.bn)


@dataclass
class Post:
    blt: int
    title: str
    post_date: str | None
    attachments: list[Attachment] = field(default_factory=list)

    @property
    def url(self) -> str:
        return BOARD_URL.format(blt=self.blt)

    @property
    def result_pdf(self) -> Optional[Attachment]:
        """'평가결과_' 로 시작하는 PDF 첨부(없으면 None — 회의자료만 있는 게시물 등)."""
        for a in self.attachments:
            if a.ext.lower() == "pdf" and a.name.startswith("평가결과"):
                return a
        return None


def parse_post(html: str, blt: int) -> Optional[Post]:
    """게시물 HTML → Post. 첨부가 하나도 없으면 None(미게시 번호·삭제·다른 유형)."""
    files = [Attachment(ext=m.group(1), name=_html.unescape(re.sub(r"\s+", " ", m.group(2))).strip(),
                        no=m.group(3), blt=m.group(4), ty=m.group(5), bn=m.group(6)) for m in _RE_FILE.finditer(html or "")]
    if not files:
        return None
    t = _RE_TITLE.search(html)
    d = _RE_DATE.search(html)
    title = _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t.group(1)))).strip() if t else ""
    return Post(blt=blt, title=title, post_date=d.group(1) if d else None, attachments=files)


def http_get(url: str, timeout: int = 30, retries: int = 2) -> bytes:
    last: Exception | None = None
    for i in range(retries + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=timeout) as r:
                return r.read()
        except Exception as e:  # 일시 오류 재시도 — 마지막 예외는 호출자가 구분한다(미게시 ≠ 접속 실패)
            last = e
            time.sleep(1.5 * (i + 1))
    raise last  # type: ignore[misc]


def discover(cursor: int, *, get: Callable[[str], bytes] = http_get, max_gap: int = 8, max_ahead: int = 120) -> tuple[list[Post], int, list[int]]:
    """cursor(마지막으로 아는 번호) 다음 번호부터 탐색. 반환: (게시물들, 새 cursor, 조회 실패 번호들).

    - 첨부 없는 번호(미게시·결번)가 연속 max_gap 개면 종료한다.
    - 접속 실패(예외)는 '게시물 없음' 으로 세지 않는다. 실패 번호 뒤에 게시물이 있을 수 있으므로 cursor 는 첫 실패 직전까지만 전진.
    - 실패가 없으면 cursor 는 마지막으로 찾은 게시물 번호. 그 뒤의 빈 번호는 다음 실행에서 다시 확인한다(게시 지연 대비).
    """
    posts: list[Post] = []
    failed: list[int] = []
    gap = 0
    for blt in range(cursor + 1, cursor + 1 + max_ahead):
        try:
            html = get(BOARD_URL.format(blt=blt)).decode("utf-8", "replace")
        except Exception:
            failed.append(blt)
            gap += 1
        else:
            post = parse_post(html, blt)
            if post:
                posts.append(post)
                gap = 0
            else:
                gap += 1
        if gap >= max_gap:
            break
    if failed:
        new_cursor = max([p.blt for p in posts if p.blt < failed[0]], default=cursor)
    else:
        new_cursor = max([p.blt for p in posts], default=cursor)
    return posts, new_cursor, failed


def download(att: Attachment, *, get: Callable[[str], bytes] = http_get) -> bytes:
    data = get(att.url)
    if not data.startswith(b"%PDF"):
        raise ValueError(f"PDF 가 아닌 응답({len(data)} bytes): {att.name}")
    return data


def safe_filename(name: str) -> str:
    """첨부 이름 → 파일명. 경로 구분자·제어문자 제거, .pdf 보장."""
    n = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip()
    return n if n.lower().endswith(".pdf") else n + ".pdf"
