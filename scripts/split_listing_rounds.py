#!/usr/bin/env python3
"""급여 파이프라인 '브랜드 1행' → '등재 차수(listing round)별' 분할 마이그레이션.

기본 = dry-run (읽기 전용 연결 `mode=ro` — DB 에 아무것도 쓰지 않는다). `--apply` 일 때만 한 트랜잭션으로 적용하고,
적용 전 손대는 행(약제·큐 이벤트·공단 협상 행·기존 차수/링크)을 JSON 으로 백업한다. 데이터 삭제 없음(추가·연결만).

1) KEYTRUDA (명시 스펙)
   - '2025 확대 · 11개 적응증'                 : 약평위 2025-09-04 이벤트 + 공단 확대 2025-10(→2025-12 합의) 행
   - '2026 확대 · 파드셉 병용 요로상피암 1차'   : 암질심 2025-10-29(급여기준 설정) + 약평위 2026-04-02 + 공단 확대 2026-05
                                                   (→2026-09 합의) 행, 급여 시행 2026-10-01. 위원회 이벤트는 파드셉 쪽 이벤트를
                                                   '공유 링크'(reimb_round_events, link_source='combo') — 파드셉에서 빼앗지 않음.
   - 파드셉 차수 '2026 신규 · 요로상피암 1차 (펨브로 병용)' 를 같은 combo_key 로 고정 (교차 참조).
   - 파드셉 2025-10-29 암질심 통과 이벤트가 없으면 1건 추가 (8차 암질심 HIRA brdBltNo=11643 — 세션 존재 시에만).
2) 일반 — 공단 협상 묶음(목록·등록연월)이 2개 이상이고 아직 차수가 없는 브랜드: reimb_pipeline.propose_split 제안.
   모호하지 않은 건만 --apply 대상, 모호한 건은 사유와 함께 목록만.

실행:
  python scripts/split_listing_rounds.py                       # dry-run (기본, 읽기 전용)
  python scripts/split_listing_rounds.py --json                # dry-run 결과 JSON
  python scripts/split_listing_rounds.py --apply               # 적용 (백업 → BEGIN IMMEDIATE → COMMIT)
  python scripts/split_listing_rounds.py --db /app/data/db/drug_prices.db
프로덕션: fly ssh console -a ma-ai-dossier -C "python scripts/split_listing_rounds.py"
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_HAD_FLAG = "REIMB_PIPELINE_NO_AUTOMIGRATE" in os.environ
os.environ.setdefault("REIMB_PIPELINE_NO_AUTOMIGRATE", "1")  # import 시 기본 DB 에 쓰지 않도록 (dry-run 읽기 전용 보장)

from agents import reimb_pipeline as rp  # noqa: E402

if not _HAD_FLAG:  # 같은 프로세스의 다른 코드(테스트 등)로 플래그가 새지 않게
    os.environ.pop("REIMB_PIPELINE_NO_AUTOMIGRATE", None)

DEFAULT_DB = ROOT / "data" / "db" / "drug_prices.db"

KEYTRUDA_SPEC = {
    "brand_kr": "키트루다",
    "partner_like": "파드셉%",
    "combo_key": "combo:keytruda+padcev:UC-1L:2026",
    "rounds": [
        {"key": "2025", "round_label": "2025 확대 · 11개 적응증", "list_type": "확대",
         "indication_scope": "11개 적응증", "yakpyungwi_pass_date": "2025-09-04",
         "nhis_ym": "2025-10", "event_until": "2025-10-31", "combo": False,
         "notes": "9차 약평위(2025-09-04) — 키트루다 11개 적응증 확대. 공단 확대 2025-10 → 합의 2025-12."},
        {"key": "2026", "round_label": "2026 확대 · 파드셉 병용 요로상피암 1차", "list_type": "확대",
         "indication_scope": "파드셉(엔포투맙 베도틴) 병용 요로상피암 1차",
         "amjilsim_pass_date": "2025-10-29", "yakpyungwi_pass_date": "2026-04-02",
         "nhis_ym": "2026-05", "reimb_start_date": "2026-10-01", "combo": True,
         "notes": "2025년 8차 암질심(2025-10-29) 급여기준 설정 → 2026년 4차 약평위(2026-04-02) → 공단 확대 2026-05 → "
                  "합의 2026-09 → 급여 2026-10-01. 파드셉 신규 등재와 같은 병용 차수(combo_key 공유)."},
    ],
    "partner_round": {"round_label": "2026 신규 · 요로상피암 1차 (펨브로 병용)", "list_type": "신규",
                      "indication_scope": "요로상피암 1차 (펨브로 병용)",
                      "amjilsim_pass_date": "2025-10-29", "yakpyungwi_pass_date": "2026-04-02",
                      "nhis_ym": "2026-05", "reimb_start_date": "2026-10-01"},
    "amjilsim_event": {"committee": "AMJILSIM", "session_date": "2025-10-29", "state": "APPROVED",
                       "evidence_url": "HIRA brdBltNo=11643 — 2025년 8차 암질심 급여기준 설정 "
                                       "(파드셉+펨브롤리주맙 요로상피암 1차)"},
}


# ── 공통 ─────────────────────────────────────────────────────────────────────

def _connect(db: Path, readonly: bool) -> sqlite3.Connection:
    if readonly:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(str(db), isolation_level=None)  # 명시 BEGIN/COMMIT
    conn.row_factory = sqlite3.Row
    return conn


def _events(conn, drug_id: int) -> list[sqlite3.Row]:
    return conn.execute(rp._QUEUE_JOIN_SQL, (drug_id,)).fetchall()


def _nhis_rows(conn, drug_id: int, ym: str | None = None) -> list[sqlite3.Row]:
    if not rp._nhis_table_exists(conn):
        return []
    if ym is None:
        return conn.execute("SELECT * FROM nhis_negotiations WHERE drug_id = ? ORDER BY registered_ym, id",
                            (drug_id,)).fetchall()
    return conn.execute("SELECT * FROM nhis_negotiations WHERE drug_id = ? AND registered_ym = ? ORDER BY id",
                        (drug_id, ym)).fetchall()


def _ev_brief(q) -> str:
    return f"#{q['id']} {rp.COMMITTEE_KR.get(q['committee_type'], q['committee_type'])} " \
           f"{rp.STATE_LABEL_KR.get(q['queue_state'], q['queue_state'])} {rp._event_date(q) or '?'}"


def _nhis_brief(n) -> str:
    done = n["completed_ym"] or "협상중"
    return f"#{n['id']} {n['list_type']} {n['registered_ym']}→{done}"


# ── KEYTRUDA 명시 스펙 ───────────────────────────────────────────────────────

def plan_keytruda(conn) -> dict:
    """읽기 전용 — 무엇을 만들고 무엇을 연결할지."""
    spec = KEYTRUDA_SPEC
    k = conn.execute("SELECT * FROM amjilsim_drugs WHERE brand_kr = ?", (spec["brand_kr"],)).fetchone()
    p = conn.execute("SELECT * FROM amjilsim_drugs WHERE brand_kr LIKE ? ORDER BY drug_id LIMIT 1",
                     (spec["partner_like"],)).fetchone()
    plan: dict = {"applicable": False, "warnings": [], "rounds": [], "partner": None, "new_event": None}
    if not k:
        plan["warnings"].append("키트루다 약제 행 없음 — 건너뜀")
        return plan
    plan["drug_id"] = k["drug_id"]
    existing = {r["round_label"]: r for r in rp.list_rounds(conn, k["drug_id"])}
    other_labels = [x for x in existing if x not in {r["round_label"] for r in spec["rounds"]}]
    if other_labels:
        plan["warnings"].append(f"키트루다에 스펙 외 차수 존재 {other_labels} — 스펙 차수만 추가/연결")
    k_events = _events(conn, k["drug_id"])
    p_events = _events(conn, p["drug_id"]) if p else []
    if not p:
        plan["warnings"].append("파드셉 약제 행 없음 — 병용 차수는 키트루다 쪽만, 공유 이벤트 없음")

    # 파드셉 2025-10-29 암질심 이벤트 (없으면 신규 1건)
    ae = spec["amjilsim_event"]
    sess = conn.execute("SELECT session_id FROM amjilsim_sessions WHERE session_date = ? AND committee_type = ?",
                        (ae["session_date"], ae["committee"])).fetchone()
    amj_existing = [q for q in p_events if q["committee_type"] == ae["committee"]
                    and rp._event_date(q) == ae["session_date"]]
    if p and not amj_existing:
        if sess:
            plan["new_event"] = {"drug_id": p["drug_id"], "session_id": sess["session_id"], **ae}
        else:
            plan["warnings"].append(f"{ae['session_date']} 암질심 세션 없음 — 암질심 이벤트 추가 생략(차수 통과일 필드만 기록)")

    for r in spec["rounds"]:
        nh = _nhis_rows(conn, k["drug_id"], r["nhis_ym"])
        if not nh:
            plan["warnings"].append(f"공단 {r['nhis_ym']} 키트루다 행 없음 — '{r['round_label']}' 는 공단 연결 없이 생성")
        held = {n["round_id"] for n in nh if rp._row_get(n, "round_id")}
        if held - {existing[r["round_label"]]["round_id"]} if r["round_label"] in existing else held:
            plan["warnings"].append(f"공단 {r['nhis_ym']} 행이 이미 다른 차수 {sorted(held)} 에 연결됨 — 명시 링크 존중, 재연결 안 함")
            nh_ids = []
        else:
            nh_ids = [n["id"] for n in nh]
        if r["key"] == "2025":
            own = [q for q in k_events if (rp._event_date(q) or "9999") <= r["event_until"]]
            shared = []
        else:
            own = [q for q in k_events if (rp._event_date(q) or "0000") > spec["rounds"][0]["event_until"]]
            shared = [q for q in p_events if (rp._event_date(q) or "0000") >= "2025-01-01"
                      and (rp._event_date(q) or "9999")[:7] <= r["nhis_ym"]] if r["combo"] else []
        plan["rounds"].append({**r, "exists": r["round_label"] in existing,
                               "round_id": existing[r["round_label"]]["round_id"] if r["round_label"] in existing else None,
                               "nhis_ids": nh_ids, "nhis": [_nhis_brief(n) for n in nh],
                               "own_event_ids": [q["id"] for q in own], "own_events": [_ev_brief(q) for q in own],
                               "shared_event_ids": [q["id"] for q in shared],
                               "shared_events": [_ev_brief(q) for q in shared]})
    if p:
        pr = spec["partner_round"]
        p_rounds = rp.list_rounds(conn, p["drug_id"])
        p_cycles = rp._nhis_cycles(conn, p["drug_id"])
        nh = _nhis_rows(conn, p["drug_id"], pr["nhis_ym"])
        plan["partner"] = {"drug_id": p["drug_id"], "brand": p["brand_kr"], **pr,
                           "existing_rounds": [r["round_label"] for r in p_rounds],
                           "nhis_ids": [n["id"] for n in nh if not rp._row_get(n, "round_id")],
                           "nhis": [_nhis_brief(n) for n in nh],
                           "event_ids": [q["id"] for q in p_events
                                         if not any(rd == p["drug_id"] for _, rd, _ in rp._event_links(conn).get(q["id"], []))],
                           "events": [_ev_brief(q) for q in p_events],
                           "multi_cycle": len(p_cycles) > 1}
    plan["applicable"] = True
    return plan


def apply_keytruda(conn, plan: dict) -> dict:
    spec = KEYTRUDA_SPEC
    did = plan["drug_id"]
    out = {"rounds": [], "new_event_id": None}
    new_eid = None
    if plan.get("new_event"):
        ne = plan["new_event"]
        dup = conn.execute("SELECT id FROM amjilsim_drug_queue_status WHERE drug_id = ? AND committee_type = ? "
                           "AND session_id = ? AND queue_state = ?",
                           (ne["drug_id"], ne["committee"], ne["session_id"], ne["state"])).fetchone()
        if dup:
            new_eid = dup["id"]
        else:
            new_eid = conn.execute(
                """INSERT INTO amjilsim_drug_queue_status
                   (drug_id, session_id, queue_state, queue_entry_date, n_th_attempt, evidence_url, committee_type)
                   VALUES (?,?,?,?,?,?,?)""",
                (ne["drug_id"], ne["session_id"], ne["state"], ne["session_date"], 1, ne["evidence_url"],
                 ne["committee"])).lastrowid
        out["new_event_id"] = new_eid
    for r in plan["rounds"]:
        fields = {k: r.get(k) for k in ("list_type", "indication_scope", "amjilsim_pass_date", "yakpyungwi_pass_date",
                                        "reimb_start_date", "notes")}
        if r["combo"]:
            fields["combo_key"] = spec["combo_key"]
        rid = rp.create_round_row(conn, did, round_label=r["round_label"], source="migration", **fields)
        for qid in r["own_event_ids"]:
            rp.link_event_to_round(conn, qid, rid, "migration")
        for qid in r["shared_event_ids"] + ([new_eid] if (r["combo"] and new_eid) else []):
            rp.link_event_to_round(conn, qid, rid, "combo")
        for nid in r["nhis_ids"]:
            rp.link_nhis_to_round(conn, nid, rid, "migration", with_siblings=False)
        rp.refresh_round_negotiation(conn, rid)
        out["rounds"].append(rid)
    pp = plan.get("partner")
    if pp:
        target = None
        for r in rp.list_rounds(conn, pp["drug_id"]):
            regs = {n["registered_ym"] for n in rp._round_nhis_rows(conn, r["round_id"])}
            if pp["nhis_ym"] in regs or r["round_label"] == pp["round_label"]:
                target = r["round_id"]
        if target is None:
            target = rp.create_round_row(
                conn, pp["drug_id"], round_label=pp["round_label"], source="migration",
                list_type=pp["list_type"], indication_scope=pp["indication_scope"], combo_key=spec["combo_key"],
                amjilsim_pass_date=pp["amjilsim_pass_date"], yakpyungwi_pass_date=pp["yakpyungwi_pass_date"],
                reimb_start_date=pp["reimb_start_date"])
        else:
            conn.execute("UPDATE reimb_listing_rounds SET combo_key = COALESCE(combo_key, ?), "
                         "amjilsim_pass_date = COALESCE(amjilsim_pass_date, ?), "
                         "reimb_start_date = COALESCE(reimb_start_date, ?) WHERE round_id = ?",
                         (spec["combo_key"], pp["amjilsim_pass_date"], pp["reimb_start_date"], target))
        for qid in pp["event_ids"] + ([new_eid] if new_eid else []):
            rp.link_event_to_round(conn, qid, target, "migration")
        for nid in pp["nhis_ids"]:
            rp.link_nhis_to_round(conn, nid, target, "migration", with_siblings=False)
        rp.refresh_round_negotiation(conn, target)
        out["partner_round"] = target
    return out


# ── 일반 브랜드 ──────────────────────────────────────────────────────────────

def plan_generic(conn, exclude: set[int]) -> list[dict]:
    if not rp._nhis_table_exists(conn):
        return []
    ids = [r["drug_id"] for r in conn.execute(
        "SELECT drug_id FROM nhis_negotiations WHERE drug_id IS NOT NULL GROUP BY drug_id "
        "HAVING COUNT(DISTINCT list_type || '|' || IFNULL(registered_ym, '')) > 1")]
    out = []
    for did in ids:
        if did in exclude:
            continue
        if not conn.execute("SELECT 1 FROM amjilsim_drugs WHERE drug_id = ?", (did,)).fetchone():
            continue
        out.append(rp.propose_split(conn, did))
    return out


# ── 백업 ─────────────────────────────────────────────────────────────────────

def backup_rows(conn, drug_ids: set[int], path: Path) -> dict:
    ids = sorted(drug_ids)
    q = ",".join("?" for _ in ids) or "NULL"
    data = {"created_at": datetime.now().isoformat(timespec="seconds"), "drug_ids": ids,
            "amjilsim_drugs": [dict(r) for r in conn.execute(f"SELECT * FROM amjilsim_drugs WHERE drug_id IN ({q})", ids)],
            "amjilsim_drug_queue_status": [dict(r) for r in conn.execute(
                f"SELECT * FROM amjilsim_drug_queue_status WHERE drug_id IN ({q})", ids)],
            "nhis_negotiations": [dict(r) for r in conn.execute(
                f"SELECT * FROM nhis_negotiations WHERE drug_id IN ({q})", ids)] if rp._nhis_table_exists(conn) else [],
            "reimb_listing_rounds": [], "reimb_round_events": []}
    if rp.rounds_enabled(conn):
        data["reimb_listing_rounds"] = [dict(r) for r in conn.execute(
            f"SELECT * FROM reimb_listing_rounds WHERE drug_id IN ({q})", ids)]
        data["reimb_round_events"] = [dict(r) for r in conn.execute(
            f"SELECT * FROM reimb_round_events WHERE round_drug_id IN ({q})", ids)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return {"path": str(path), "rows": {k: len(v) for k, v in data.items() if isinstance(v, list) and k != "drug_ids"}}


# ── 출력 ─────────────────────────────────────────────────────────────────────

def print_report(rep: dict) -> None:
    print(f"{'[APPLY]' if rep['apply'] else '[DRY-RUN]'} split_listing_rounds — DB={rep['db']}"
          f"{'' if rep['apply'] else ' (read-only)'}")
    sch = rep["schema"]
    print(f"schema: reimb_listing_rounds={'present' if sch['rounds'] else 'absent (apply 시 생성)'}, "
          f"nhis_negotiations.round_id={'present' if sch['nhis_round_col'] else 'absent (apply 시 ALTER ADD)'}")
    kp = rep["keytruda"]
    print("\n== KEYTRUDA 명시 스펙 ==")
    for w in kp.get("warnings", []):
        print(f"  ! {w}")
    for r in kp.get("rounds", []):
        print(f"  {'=' if r['exists'] else '+'} [{r['list_type']}] '{r['round_label']}'"
              f"{' (combo ' + KEYTRUDA_SPEC['combo_key'] + ')' if r['combo'] else ''}")
        dates = " · ".join(x for x in (
            f"암질심 {r['amjilsim_pass_date']}" if r.get("amjilsim_pass_date") else None,
            f"약평위 {r['yakpyungwi_pass_date']}" if r.get("yakpyungwi_pass_date") else None,
            f"급여 {r['reimb_start_date']}" if r.get("reimb_start_date") else None) if x)
        print(f"      {dates}")
        print(f"      공단: {', '.join(r['nhis']) or '없음'}")
        print(f"      이벤트(키트루다): {', '.join(r['own_events']) or '없음'}")
        if r["combo"]:
            print(f"      이벤트(파드셉 공유 링크): {', '.join(r['shared_events']) or '없음'}")
    if kp.get("new_event"):
        ne = kp["new_event"]
        print(f"  + 이벤트 신규: 파드셉 암질심 {ne['state']} {ne['session_date']} (session {ne['session_id']}) "
              f"→ 파드셉 차수 + 키트루다 2026 차수에 연결")
    if kp.get("partner"):
        pp = kp["partner"]
        print(f"  {'=' if pp['round_label'] in pp['existing_rounds'] else '+'} 파트너 {pp['brand']}(drug_id={pp['drug_id']}) "
              f"'{pp['round_label']}' combo · 공단: {', '.join(pp['nhis']) or '없음'} · 이벤트: {', '.join(pp['events']) or '없음'}")
    print("\n== 일반: 공단 협상 묶음 2개 이상 브랜드 ==")
    if not rep["generic"]:
        print("  (해당 없음)")
    for g in rep["generic"]:
        cyc = ", ".join(f"{c['list_type']} {c['registered_ym']}→{c['completed_ym'] or '협상중'}" for c in g["cycles"])
        if g["existing_rounds"]:
            tag = "SKIP(이미 분할)"
        else:
            tag = "AMBIGUOUS" if g["ambiguous"] else "APPLY"
        print(f"  {tag:<15} {g['brand']} (drug_id={g['drug_id']}) — 공단 {cyc}")
        for r in g["rounds"]:
            print(f"      → '{r['round_label']}'{' [검토 필요]' if r['needs_review'] else ''} "
                  f"공단 {r['nhis_ids']} 이벤트 {r['event_ids']}"
                  f"{' 파트너 이벤트 ' + str(r['partner_event_ids']) if r['partner_event_ids'] else ''}")
        for why in g["reasons"]:
            print(f"      ! {why}")
    s = rep["summary"]
    print(f"\nsummary: keytruda_rounds={s['keytruda_rounds']} (new {s['keytruda_new']}), "
          f"generic_applicable={s['generic_applicable']}, generic_ambiguous={s['generic_ambiguous']}, "
          f"generic_skipped={s['generic_skipped']}")
    if rep.get("backup"):
        print(f"backup: {rep['backup']['path']} {rep['backup']['rows']}")
    if rep.get("applied"):
        print(f"applied: {json.dumps(rep['applied'], ensure_ascii=False)}")


def run(db: Path, apply: bool = False, backup_dir: Path | None = None) -> dict:
    conn = _connect(db, readonly=not apply)
    try:
        k_plan = plan_keytruda(conn)
        exclude = {x for x in (k_plan.get("drug_id"), (k_plan.get("partner") or {}).get("drug_id")) if x}
        generic = plan_generic(conn, exclude)
        rep = {"apply": apply, "db": str(db),
               "schema": {"rounds": rp.rounds_enabled(conn), "nhis_round_col": rp._nhis_has_round_col(conn)},
               "keytruda": k_plan, "generic": generic,
               "summary": {"keytruda_rounds": len(k_plan.get("rounds", [])),
                           "keytruda_new": sum(1 for r in k_plan.get("rounds", []) if not r["exists"]),
                           "generic_applicable": sum(1 for g in generic if not g["ambiguous"] and not g["existing_rounds"]
                                                     and g["rounds"]),
                           "generic_ambiguous": sum(1 for g in generic if g["ambiguous"]),
                           "generic_skipped": sum(1 for g in generic if g["existing_rounds"])}}
        if not apply:
            return rep
        touched = set(exclude)
        for g in generic:
            if not g["ambiguous"] and not g["existing_rounds"] and g["rounds"]:
                touched.add(g["drug_id"])
                touched.update(pr["drug_id"] for pr in g.get("partner_rounds", []))
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        bdir = backup_dir or (db.parent.parent / "backups")
        rep["backup"] = backup_rows(conn, touched, bdir / f"split_listing_rounds_{stamp}.json")
        conn.execute("BEGIN IMMEDIATE")
        try:
            rp.ensure_round_schema(conn)
            applied: dict = {"keytruda": None, "generic": []}
            if k_plan.get("applicable"):
                k_plan = plan_keytruda(conn)  # 스키마 생성 후 재계산 (같은 트랜잭션)
                applied["keytruda"] = apply_keytruda(conn, k_plan)
            for g in plan_generic(conn, exclude):
                if g["ambiguous"] or g["existing_rounds"] or not g["rounds"]:
                    continue
                applied["generic"].append(rp.apply_split_plan(conn, g, source="migration"))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        rep["applied"] = applied
        return rep
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--apply", action="store_true", help="적용 (기본 dry-run, 읽기 전용)")
    ap.add_argument("--backup-dir", type=Path, default=None, help="백업 JSON 위치 (기본 <db>/../../backups)")
    ap.add_argument("--json", action="store_true", help="결과를 JSON 으로 출력")
    args = ap.parse_args(argv)
    if not args.db.exists():
        print(f"DB 없음: {args.db}", file=sys.stderr)
        return 2
    rep = run(args.db, apply=args.apply, backup_dir=args.backup_dir)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    else:
        print_report(rep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
