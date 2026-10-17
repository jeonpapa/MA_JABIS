"""platform_smoke 거짓 실패 회귀 — 한글 질의 경로가 UnicodeEncodeError 없이 요청돼야 한다."""
import unittest
from scripts import smoke


class _Resp:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return b'{"ok": true}'


class SmokeKoreanQueryTests(unittest.TestCase):
    def test_korean_query_is_percent_encoded(self):
        seen = {}
        def opener(req, timeout=10):
            seen["url"] = req.full_url
            req.full_url.encode("ascii")  # 인코딩 실패 시 여기서 예외
            return _Resp()
        status, body = smoke._get("http://x", "/api/domestic/search?q=키트루다", None, opener, 5)
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True})
        self.assertIn("q=%ED%82%A4", seen["url"])

    def test_already_encoded_path_unchanged(self):
        seen = {}
        def opener(req, timeout=10):
            seen["url"] = req.full_url
            return _Resp()
        smoke._get("http://x", "/api/x?q=%ED%82%A4&a=b", None, opener, 5)
        self.assertEqual(seen["url"], "http://x/api/x?q=%ED%82%A4&a=b")


if __name__ == "__main__":
    unittest.main()
