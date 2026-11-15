"""프랑스 BdM_IT(Assurance Maladie 「Base des Médicaments et Informations Tarifaires」) — 서버 수집 (2026-11-10 사용자 결정).

검증(2026-11-10, 운영 서버): 관보(JO) 기준값 10건 전부 일치 — CIP 9건(윈레브에어 4·프리비미스 4·웰리렉 1)의
Prix Fabricant HT, 키트루다 100mg UCD 의 TAA MCO(liste en sus) Prix HT €2,000.74(적용 2026-04-03, JO 2026-03-31).

- 약국 유통(CIP): fiche/index_fic_medisoc.php — "Historique Remboursement" 의 Prix Fabricant HT(=관보 PFHT)·Prix Public TTC·
  Taux·Date d'application·Date d'opposabilité PPTTC·Date JO.
- 병원 전용(UCD): fiche/index_fic_ucd.php — "Historique pour la TAA MCO" 의 Prix HT(=관보 tarif de responsabilité HT).
  같은 화면의 rétrocession 칸은 가격 없이 등록만 된 경우가 있다(키트루다 2026-08-01 '가격 -' 행) — 가격으로 쓰지 않는다.
- **시행일 = Date d'application** (2026-11-10 사용자 결정). 판매가 기준일·관보일은 extra 에 함께 보관.
- 사이트가 안 열리면 BdmItUnavailable — 워커는 브라우저로 관보(Légifrance)를 연다.
표준 라이브러리만 사용(워커 스크립트와 공용).
"""
from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

BASE = "http://www.codage.ext.cnamts.fr/codif/bdm_it"
UA = {"User-Agent": "Mozilla/5.0 (MA-AI-Dossier price check)"}
KST = timezone(timedelta(hours=9))
RETRIES = 3
RETRY_WAIT = 4   # 초(시도마다 늘림)


class BdmItUnavailable(RuntimeError):
    pass


def _fetch(url: str, data: bytes | None = None, timeout: int = 30) -> tuple[str, str]:
    """(본문, 최종 URL). 검색 결과가 1건이면 사이트가 상세 화면으로 바로 넘긴다 — 최종 URL 에 코드가 있다."""
    # 운영 서버(Fly)에서 도메인 조회 실패가 간헐적으로 난다(2026-11-10 시험: 3회 연속 후 성공) — 3번까지 다시 시도한다.
    import time
    last = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, data=data, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                b, final = r.read(), r.geturl()
            try:
                return b.decode("utf-8"), final
            except UnicodeDecodeError:
                return b.decode("latin-1"), final
        except Exception as e:
            last = e
            if attempt < RETRIES - 1:
                time.sleep(RETRY_WAIT * (attempt + 1))
    raise BdmItUnavailable(f"BdM_IT 접속 실패({RETRIES}회): {type(last).__name__}: {str(last)[:120]}") from last


def _get(url: str, data: bytes | None = None, timeout: int = 30) -> str:
    return _fetch(url, data, timeout)[0]


def _text(raw: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw)))


def _d(s: str) -> date:
    return datetime.strptime(s, "%d/%m/%Y").date()


def _iso(s: str | None) -> str | None:
    return _d(s).isoformat() if s and re.fullmatch(r"\d\d/\d\d/\d{4}", s) else None


def ucd13(ucd7: str) -> str:
    base = "34008" + ucd7
    total = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(base))
    return base + str((10 - total % 10) % 10)


def search(brand: str) -> dict:
    """상표명 → {"cip": [CIP13…], "ucd": [UCD7…]} (BdM_IT 이름 검색)."""
    out = {}
    for kind, flags in (("cip", {"p_cip": "cip", "p_homol_ass": "ass", "p_homol_coll": "coll"}),
                        ("ucd", {"p_ucd": "ucd", "p_homol_retro": "retro", "p_homol_taa": "taa", "p_homol_taa_ssr": "taa_ssr"})):
        data = urllib.parse.urlencode({"p_code_cip": "", "p_nom_commercial": brand.upper(), **flags}).encode()
        raw, final = _fetch(f"{BASE}/fiche/bdm_it_test_nb_enreg.php?p_site=AMELI", data)
        codes = re.findall(r"p_code_cip=(\d{7,13})", final + " " + raw)
        out[kind] = list(dict.fromkeys(c for c in codes if (len(c) == 13) == (kind == "cip")))
    return out


def _latest(rows: list[tuple], app_idx: int, today: date):
    """적용일 기준 최신(오늘 이전) 행 + 오늘 이후 예정 행."""
    past = [r for r in rows if _d(r[app_idx]) <= today]
    future = [r for r in rows if _d(r[app_idx]) > today]
    cur = max(past, key=lambda r: _d(r[app_idx])) if past else None
    nxt = min(future, key=lambda r: _d(r[app_idx])) if future else None
    return cur, nxt


def cip_fiche(cip: str, today: date | None = None) -> dict:
    today = today or datetime.now(KST).date()
    url = f"{BASE}//fiche/index_fic_medisoc.php?p_code_cip={cip}&p_site=AMELI"
    t = _text(_get(url))
    if f"CIP : {cip}" not in t:
        raise BdmItUnavailable(f"BdM_IT CIP {cip} 상세 화면 형식이 다르거나 없음")
    name = re.search(r"Désignation : (.+?) Conditionnement : (.+?) Grand Conditionnement", t)
    assures = t.split("Historique pour les collectivit")[0]
    seg = assures.split("Historique Remboursement", 1)[1] if "Historique Remboursement" in assures else ""
    rows = re.findall(r"(\d+\.\d{3}) € (\d+\.\d{2}) € (\d+) % (\d\d/\d\d/\d{4}) (\S+) (\d\d/\d\d/\d{4})", seg)
    cur, nxt = _latest(rows, 3, today)
    return {"cip13": cip, "url": url, "designation": name.group(1).strip() if name else None,
            "conditionnement": name.group(2).strip() if name else None,
            "pfht": float(cur[0]) if cur else None, "ppttc": float(cur[1]) if cur else None, "taux": int(cur[2]) if cur else None,
            "date_application": _iso(cur[3]) if cur else None, "date_opposabilite": _iso(cur[4]) if cur else None,
            "date_jo": _iso(cur[5]) if cur else None, "history_rows": len(rows),
            "next": {"pfht": float(nxt[0]), "date_application": _iso(nxt[3])} if nxt else None}


def ucd_fiche(ucd7: str, today: date | None = None) -> dict:
    today = today or datetime.now(KST).date()
    url = f"{BASE}/fiche/index_fic_ucd.php?p_code_cip={ucd7}&p_site=AMELI"
    t = _text(_get(url))
    if f"UCD : {ucd7}" not in t:
        raise BdmItUnavailable(f"BdM_IT UCD {ucd7} 상세 화면 형식이 다르거나 없음")
    taa = t.split("Historique pour la TAA MCO", 1)[1].split("Historique pour la TAA SSR")[0] if "Historique pour la TAA MCO" in t else ""
    seg = taa.split("Historique Remboursement", 1)[1] if "Historique Remboursement" in taa else ""
    rows = re.findall(r"(\d\d/\d\d/\d{4}) (\d+\.\d{3}) € (\d+\.\d{2}) € ([\d.]+) % (\d\d/\d\d/\d{4}) (\w+)", seg)
    cur, nxt = _latest(rows, 0, today)
    desig = re.search(r"Désignation : (.+?) Observations", t)
    return {"ucd7": ucd7, "ucd13": ucd13(ucd7), "url": url,
            "designation": _dedup(desig.group(1).strip()) if desig else None,
            "prix_ht": float(cur[1]) if cur else None, "prix_ttc": float(cur[2]) if cur else None, "tva": cur[3] if cur else None,
            "date_application": _iso(cur[0]) if cur else None, "date_source": _iso(cur[4]) if cur else None,
            "source_doc": cur[5] if cur else None, "history_rows": len(rows),
            "next": {"prix_ht": float(nxt[1]), "date_application": _iso(nxt[0])} if nxt else None}


def _dedup(s: str) -> str:
    """'KEYTRUDA 100 MG FL KEYTRUDA 100 MG FL' (지정명·약어 반복) → 앞 절반."""
    words = s.split()
    n = len(words)
    if n % 2 == 0 and words[: n // 2] == words[n // 2:]:
        return " ".join(words[: n // 2])
    return s


UNIT_WORDS = r"FLACONS?|SERINGUES?(?:\s+PR[EÉ]REMPLIES?)?|STYLOS?|AMPOULES?|SACHETS?|COMPRIM[EÉ]S?|G[EÉ]LULES?|CAPSULES?|POCHES?|CARTOUCHES?"


def pack_count(conditionnement: str | None) -> int | None:
    """포장 수(정·바이알 등 최소단위). 읽을 수 없으면 None — 추정하지 않는다.
    '(B/3X30)' → 90 · '(B/28)' → 28 · '1 BOITE DE 28, COMPRIMÉS' → 28 · '2 FLACONS DE 45 MG … ET 2 SERINGUES' → 2(첫 단위어)."""
    t = (conditionnement or "").upper()
    m = re.search(r"\(\s*B\s*/\s*(\d+)\s*(?:[X×]\s*(\d+))?", t)
    if m:
        return int(m.group(1)) * (int(m.group(2)) if m.group(2) else 1)
    m = re.search(r"(?:(\d+)\s+)?BO[IÎ]TES?\s+DE\s+(\d+)", t)
    if m:
        return int(m.group(1) or 1) * int(m.group(2))
    m = re.search(r"\b(\d+)\s+(?:" + UNIT_WORDS + r")\b", t)
    if m:
        return int(m.group(1))
    return None


def form_type(text: str) -> str:
    t = (text or "").upper()
    if re.search(r"INJ|PERF|FLACON|SERINGUE|STYLO|AMPOULE|\bFL\b", t):
        return "injection"
    if re.search(r"COMPRIM|GELULE|GÉLULE|CAPSULE|GRANUL|SACHET|BUVABLE|SIROP|CPR", t):
        return "oral"
    return "unknown"


def _strength(text: str) -> str:
    m = re.search(r"\d+(?:[.,]\d+)?\s*(?:MG|G|µG|MICROGRAMMES?)(?:\s*/\s*\d*(?:[.,]\d+)?\s*ML)?", (text or "").upper())
    return m.group(0).lower() if m else ""


def rows_for(brand: str, ingredient: str = "", observed_at: str | None = None) -> tuple[list[dict], list[str]]:
    """업로드 행(`price_request_client upload` 형식). CIP 공장도가가 있으면 CIP, 없을 때만 UCD(병원 전용)."""
    observed_at = observed_at or datetime.now(KST).isoformat(timespec="seconds")
    found = search(brand)
    rows, skipped = [], []
    for cip in found["cip"]:
        f = cip_fiche(cip)
        if f["pfht"] is None:
            skipped.append(f"CIP {cip} {f['designation'] or ''}: 급여 가격 이력 없음(비급여·미등재 가능)")
            continue
        pc = pack_count(f["conditionnement"])
        if not pc:
            skipped.append(f"CIP {cip}: 포장 수를 읽을 수 없음 ({f['conditionnement']}) — 브라우저 확인")
            continue
        text = f"{f['designation']} {f['conditionnement']}"
        rows.append({
            "product_name": f["designation"], "ingredient": ingredient or brand, "dosage_strength": _strength(f["designation"]) or _strength(text) or "-",
            "dosage_form": re.sub(r"\s*\(B/.*$", "", f["conditionnement"] or "").strip() or "-",
            "package_unit": f["conditionnement"], "pack_count": pc, "local_price": f["pfht"], "currency": "EUR",
            "price_tier": "ex_factory", "price_tier_original": "PFHT (Prix Fabricant HT, BdM_IT = Journal officiel)",
            "vat_status": "excluded", "effective_date": f["date_application"], "observed_at": observed_at,
            "source_url": f["url"], "source_label": "BdM_IT Assurance Maladie (PFHT, 관보 공시가)",
            "evidence_ref": f"server-fetch:bdm_it:CIP {cip}", "form_type": form_type(text),
            "extra": {"cip13": cip, "ppttc_reference": f["ppttc"], "taux_remboursement": f["taux"], "date_application": f["date_application"],
                      "date_opposabilite_ppttc": f["date_opposabilite"], "date_jo": f["date_jo"], "next_price": f["next"],
                      "effective_date_basis": "Date d'application (2026-11-10 사용자 결정)", "server_collected": True},
        })
    # 같은 제품·포장 수·가격이 CIP 만 다르게 중복 등록된 경우(키트 vs 단품 표기 등) 하나만 남긴다
    seen, uniq = set(), []
    for r in rows:
        k = (r["product_name"], r["pack_count"], r["local_price"])
        if k in seen:
            skipped.append(f"CIP {r['extra']['cip13']}: 같은 제품·포장·가격 중복 — 생략")
            continue
        seen.add(k)
        uniq.append(r)
    rows = uniq
    if not rows:
        for ucd in found["ucd"]:
            f = ucd_fiche(ucd)
            if f["prix_ht"] is None:
                skipped.append(f"UCD {ucd} {f['designation'] or ''}: TAA MCO(liste en sus) 가격 없음")
                continue
            rows.append({
                "product_name": f["designation"], "ingredient": ingredient or brand, "dosage_strength": _strength(f["designation"]) or "-",
                "dosage_form": f["designation"], "package_unit": f"1 UCD ({f['designation']})", "pack_count": 1,
                "local_price": f["prix_ht"], "currency": "EUR", "price_tier": "ex_factory",
                "price_tier_original": "Tarif de responsabilité HT par UCD (BdM_IT TAA MCO, L.162-22-7 liste en sus)",
                "vat_status": "excluded", "effective_date": f["date_application"], "observed_at": observed_at,
                "source_url": f["url"], "source_label": "BdM_IT Assurance Maladie (UCD tarif HT, 관보 공시가)",
                "evidence_ref": f"server-fetch:bdm_it:UCD {ucd}", "form_type": form_type(f["designation"] or ""),
                "extra": {"ucd7": ucd, "ucd13": f["ucd13"], "prix_ttc_reference": f["prix_ttc"], "tva": f["tva"],
                          "date_application": f["date_application"], "date_source_jo": f["date_source"], "next_price": f["next"],
                          "effective_date_basis": "Date d'application (2026-11-10 사용자 결정)", "server_collected": True},
            })
    if not found["cip"] and not found["ucd"]:
        skipped.append(f"BdM_IT 상표명 '{brand}' 검색 0건(CIP·UCD)")
    return rows, skipped
