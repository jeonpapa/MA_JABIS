"""분석 요청(A안) — 저장소·API 권한·lease·한도·결과 공개 범위 (2026-11-02).

실행: .venv/bin/python -m pytest tests/test_analysis_requests.py -q
"""
from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from flask import Flask

from api.auth import _issue_token
from api.price_request_routes import build_price_request_blueprint

Q = "2022~2026 약평위에서 대체약제 가중평균가 대비 90% 미만으로 등재된 아날로그를 찾아 주세요"


def H(email, role):
    return {"Authorization": "Bearer " + _issue_token(email, role)}


class AnalysisRequestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "t.db"
        app = Flask(__name__)
        app.testing = True
        app.register_blueprint(build_price_request_blueprint(self.path, None))
        self.c = app.test_client()
        self.u1, self.u2 = H("a@example.org", "user"), H("b@example.org", "user")
        self.admin, self.w = H("admin@example.org", "admin"), H("worker@example.org", "price_agent")

    def tearDown(self):
        self.tmp.cleanup()

    def _create(self, h=None, **kw):
        r = self.c.post("/api/analysis-requests", json={"question": Q, **kw}, headers=h or self.u1)
        self.assertEqual(r.status_code, 201, r.get_json())
        return r.get_json()["id"]

    def _claim(self, rid):
        r = self.c.post(f"/api/analysis-agent/requests/{rid}/claim", headers=self.w)
        self.assertEqual(r.status_code, 200, r.get_json())
        return {**self.w, "X-Analysis-Lease": r.get_json()["lease_token"]}

    def _complete(self, rid, lh, **kw):
        body = {"summary": "90% 미만 10건: 오테밀라(55%) 등. 상세는 첨부 엑셀.", "method": "IQVIA 점유율 × 상한금액 가중평균",
                "data_as_of": "원본 DB 2026-11-02 (IQVIA ~2025Q4)", "sources": [{"name": "analog_reports"}, "market_share_quarterly"],
                "assumptions": ["2026 심의는 2025Q4 점유율 사용"], **kw}
        return self.c.post(f"/api/analysis-agent/requests/{rid}/complete", json=body, headers=lh)

    def test_create_validation_and_service_roles_blocked(self):
        self.assertEqual(self.c.post("/api/analysis-requests", json={"question": "짧음"}, headers=self.u1).status_code, 400)
        self.assertEqual(self.c.post("/api/analysis-requests", json={"question": Q + " password: x"}, headers=self.u1).status_code, 400)
        self.assertEqual(self.c.post("/api/analysis-requests", json={"question": Q}, headers=self.w).status_code, 403)
        self.assertEqual(self.c.get("/api/analysis-agent/requests", headers=self.u1).status_code, 403)
        rid = self._create(context_url="/analog-search")
        self.assertEqual(self.c.get(f"/api/analysis-requests/{rid}", headers=self.u1).get_json()["status"], "queued")

    def test_daily_and_open_limits(self):
        for _ in range(3):
            self._create()
        self.assertEqual(self.c.post("/api/analysis-requests", json={"question": Q}, headers=self.u1).status_code, 409)
        self._create(h=self.u2)                                    # 다른 사용자는 별도 한도

    def test_full_flow_ask_reply_complete_and_visibility(self):
        rid = self._create()
        private = self._create(share_team=False)
        # 진행 중에는 다른 사용자에게 안 보임
        self.assertEqual(self.c.get(f"/api/analysis-requests/{rid}", headers=self.u2).status_code, 404)
        lh = self._claim(rid)
        # 동시에 두 건 claim 불가
        self.assertEqual(self.c.post(f"/api/analysis-agent/requests/{private}/claim", headers=self.w).status_code, 409)
        self.assertEqual(self.c.post(f"/api/analysis-agent/requests/{rid}/heartbeat", headers=lh).status_code, 200)
        # 되묻기 → 요청자 답변 → 다시 접수
        r = self.c.post(f"/api/analysis-agent/requests/{rid}/ask", json={"question": "기간을 2022년부터로 보면 될까요?"}, headers=lh)
        self.assertEqual(r.get_json()["status"], "needs_info")
        self.assertEqual(self.c.post(f"/api/analysis-requests/{rid}/reply", json={"reply": "네"}, headers=self.u2).status_code, 404)
        self.assertEqual(self.c.post(f"/api/analysis-requests/{rid}/reply", json={"reply": "네, 2022년부터"}, headers=self.u1)
                         .get_json()["status"], "queued")
        self.assertEqual(self.c.post(f"/api/analysis-agent/requests/{rid}/heartbeat", headers=lh).status_code, 409)   # 옛 lease 무효
        lh = self._claim(rid)
        f = self.c.post(f"/api/analysis-agent/requests/{rid}/files", headers=lh,
                        json={"filename": "result.xlsx", "content_b64": base64.b64encode(b"PK\x03\x04data").decode()})
        self.assertEqual(f.status_code, 200, f.get_json())
        bad = self.c.post(f"/api/analysis-agent/requests/{rid}/files", headers=lh,
                          json={"filename": "x.exe", "content_b64": base64.b64encode(b"MZ").decode()})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self._complete(rid, lh, sources=[]).status_code, 400)              # 출처 없는 결과 거부
        done = self._complete(rid, lh).get_json()
        self.assertEqual(done["status"], "completed")
        self.assertEqual(len(done["files"]), 1)
        # 완료 + 팀 공개 → 다른 사용자도 보고 파일 다운로드, 이벤트는 요청자·admin 만
        other = self.c.get(f"/api/analysis-requests/{rid}", headers=self.u2).get_json()
        self.assertEqual(other["result_summary"][:3], "90%")
        self.assertNotIn("events", other)
        self.assertIn("events", self.c.get(f"/api/analysis-requests/{rid}", headers=self.u1).get_json())
        dl = self.c.get(f"/api/analysis-requests/{rid}/files/{done['files'][0]['id']}", headers=self.u2)
        self.assertEqual((dl.status_code, dl.data), (200, b"PK\x03\x04data"))
        # 나만 보기 요청은 다른 사용자 목록에 없음, admin 은 봄
        ids_u2 = {x["id"] for x in self.c.get("/api/analysis-requests", headers=self.u2).get_json()["items"]}
        self.assertIn(rid, ids_u2)
        self.assertNotIn(private, ids_u2)
        self.assertIn(private, {x["id"] for x in self.c.get("/api/analysis-requests", headers=self.admin).get_json()["items"]})
        # 완료 후에는 lease·완료 재제출 불가
        self.assertEqual(self._complete(rid, lh).status_code, 409)

    def test_cancel_and_fail(self):
        rid = self._create()
        self.assertEqual(self.c.post(f"/api/analysis-requests/{rid}/cancel", headers=self.u2).status_code, 404)
        self.assertEqual(self.c.post(f"/api/analysis-requests/{rid}/cancel", headers=self.u1).get_json()["status"], "cancelled")
        self.assertEqual(self.c.post(f"/api/analysis-agent/requests/{rid}/claim", headers=self.w).status_code, 409)
        rid2 = self._create()
        lh = self._claim(rid2)
        r = self.c.post(f"/api/analysis-agent/requests/{rid2}/fail", json={"reason": "필요한 2026년 IQVIA 자료가 DB에 없음"}, headers=lh)
        self.assertEqual(r.get_json()["status"], "failed")
        self.assertEqual(self.c.get(f"/api/analysis-requests/{rid2}", headers=self.u2).status_code, 404)   # 실패는 비공개


if __name__ == "__main__":
    unittest.main()
