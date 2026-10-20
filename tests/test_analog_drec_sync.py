"""신규 약평위 반영 파이프라인(drec_sync) — dry-run 이 DB 를 바꾸지 않고, 접속 실패·다운로드 실패를 결과에 남기는지. 네트워크·PDF 파서 없음."""
import sqlite3

from agents.analog import drec_sync
from tests.test_analog_drec_collector import EMPTY_HTML, POST_HTML


def _db(tmp_path, post_blt=47094, meta_blt=47094):
    p = tmp_path / "d.db"
    with sqlite3.connect(p) as c:
        c.execute("CREATE TABLE analog_reports (id INTEGER PRIMARY KEY, file_name TEXT, post_blt_no TEXT)")
        c.execute("CREATE TABLE yakpyungwi_meta (blt_no TEXT, product_name TEXT)")
        c.execute("INSERT INTO analog_reports (file_name, post_blt_no) VALUES ('a.pdf', ?)", (str(post_blt),))
        c.execute("INSERT INTO yakpyungwi_meta VALUES (?, 'x')", (str(meta_blt),))
    return p


def _get_factory(pages):
    def get(url: str) -> bytes:
        if "bbsCDownLoad" in url:
            return b"%PDF-1.4 fake"
        blt = int(url.split("brdBltNo=")[1].split("&")[0])
        v = pages.get(blt, EMPTY_HTML)
        if isinstance(v, Exception):
            raise v
        return v.encode()
    return get


def test_known_cursor_is_max_of_analog_and_meta(tmp_path):
    a = tmp_path / "a"; a.mkdir(); b = tmp_path / "b"; b.mkdir()
    assert drec_sync.known_cursor(_db(a, post_blt=47090, meta_blt=47094)) == 47094
    assert drec_sync.known_cursor(_db(b, post_blt=47100, meta_blt=47094)) == 47100


def test_dry_run_collects_preview_and_does_not_write(tmp_path, monkeypatch):
    p = _db(tmp_path)
    monkeypatch.setattr(drec_sync, "build_report", lambda post, path: {"brand_name": "브리베타정", "session_date": "2026-07-09",
                                                                      "review_result": "APPROVED", "_post": {"report_effect_text": "뇌전증"}})
    monkeypatch.setattr(drec_sync, "ingest_corpus", lambda *a, **k: (_ for _ in ()).throw(AssertionError("dry-run 은 쓰지 않는다")))
    res = drec_sync.run(apply=False, get=_get_factory({47095: POST_HTML, 47096: TimeoutError("x")}), db_path=p, incoming=tmp_path / "in")
    assert res["mode"] == "dry-run" and res["posts_found"] == 1 and res["failed_fetch"][:1] == [47096]
    assert res["cursor_before"] == 47094 and res["cursor_after"] == 47095
    assert res["preview"][0]["brand_name"] == "브리베타정" and res["preview"][0]["blt"] == 47095
    assert (tmp_path / "in").exists() and list((tmp_path / "in").glob("*.pdf"))          # 증빙용으로 PDF 보관
    with sqlite3.connect(p) as c:
        assert c.execute("SELECT COUNT(*) FROM analog_reports").fetchone()[0] == 1


def test_parse_failure_is_recorded_not_raised(tmp_path, monkeypatch):
    p = _db(tmp_path)

    def boom(post, path):
        raise ValueError("깨진 PDF")
    monkeypatch.setattr(drec_sync, "build_report", boom)
    res = drec_sync.run(apply=False, get=_get_factory({47095: POST_HTML}), db_path=p, incoming=tmp_path / "in")
    assert res["posts_found"] == 1 and res["preview"] == [] and res["errors"][0]["blt"] == 47095
