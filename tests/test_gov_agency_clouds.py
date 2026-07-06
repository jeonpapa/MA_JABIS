"""A1 — 정부기관별 최근 7일 빈도 클라우드 (agents.gov_agency_clouds).

순수 함수(build_agency_clouds / _tokenize)만 검증 — DB/네트워크 불필요.
"""
from agents.gov_agency_clouds import (
    AGENCY_LABELS,
    _strip_josa,
    _tokenize,
    build_agency_clouds,
)

KEEP = frozenset({"약가", "급여", "재평가", "허가"})
ASCII_KEEP = frozenset({"RSA"})


# ── 토큰화 ────────────────────────────────────────────────────────────────────

def test_tokenize_extracts_korean_nouns_and_strips_josa():
    text = "정부가 약가를 인하했다. 급여 적정성 재평가와 RSA 확대"
    toks = _tokenize(text, KEEP, ASCII_KEEP)
    assert "정부" in toks          # '정부가' → 조사 strip
    assert "약가" in toks          # keep 힌트 — '약가를' → '약가'
    assert "인하" in toks          # '인하했다' → 어미 strip
    assert "급여" in toks
    assert "재평가" in toks        # keep 힌트 보호 — '재평가' 가 '재평'으로 깨지지 않음
    assert "적정성" in toks
    assert "RSA" in toks           # ASCII 는 keep 등록 약어만
    assert "확대" in toks


def test_tokenize_drops_stopwords_numbers_and_unknown_ascii():
    text = "이번 지난해 관계자에 따르면 3000억 규모 발표 예정 hello world"
    toks = _tokenize(text, KEEP, ASCII_KEEP)
    assert "이번" not in toks
    assert "지난해" not in toks
    assert "관계자" not in toks
    assert "발표" not in toks
    assert "예정" not in toks
    assert "hello" not in toks and "HELLO" not in toks  # keep 미등록 ASCII 배제
    assert all(not t.isdigit() for t in toks)           # 숫자 없음 (정규식상 배제)
    assert "규모" in toks


def test_strip_josa_keeps_short_bases_intact():
    # 잔여 2글자 미만 → strip 하지 않음 ('평가' → '평' 방지)
    assert _strip_josa("평가", frozenset()) == "평가"
    assert _strip_josa("심평원이", frozenset()) == "심평원"
    # keep 힌트는 원형 유지
    assert _strip_josa("재평가", KEEP) == "재평가"


# ── 기관별 그룹핑/집계 ────────────────────────────────────────────────────────

def _rows():
    return [
        {"brand": "보건복지부", "title": "약가 인하 정책 시행", "description": "복지부, 약가 제도 개편",
         "url": "http://a/1", "naver_link": "", "source_name": "데일리팜", "pub_date": "2026-07-05"},
        {"brand": "보건복지부", "title": "약가 협상 지침 개정", "description": "",
         "url": "http://a/2", "naver_link": "", "source_name": "히트뉴스", "pub_date": "2026-07-04"},
        {"brand": "NATIONAL_ASSEMBLY", "title": "국정감사 급여 질의", "description": "급여 확대 요구",
         "url": "http://a/3", "naver_link": "http://n/3", "source_name": "", "pub_date": "2026-07-03"},
    ]


def test_build_groups_by_agency_pure_frequency():
    clouds = build_agency_clouds(
        _rows(), ["보건복지부", "NATIONAL_ASSEMBLY", "PATIENT_GROUP"], KEEP, ASCII_KEEP)
    # 기사 0건 기관(PATIENT_GROUP)은 생략, 입력 순서 유지
    assert [c["agency"] for c in clouds] == ["보건복지부", "NATIONAL_ASSEMBLY"]

    mohw = clouds[0]
    assert mohw["article_count"] == 2
    counts = {k["text"]: k["count"] for k in mohw["keywords"]}
    assert counts["약가"] == 3                      # 제목 2 + 본문 1 — 순수 빈도
    assert "복지부" not in counts                    # 기관 자기 이름 토큰 제외
    # 빈도 내림차순
    vals = [k["count"] for k in mohw["keywords"]]
    assert vals == sorted(vals, reverse=True)


def test_s4_english_tags_relabelled_korean():
    clouds = build_agency_clouds(_rows(), ["보건복지부", "NATIONAL_ASSEMBLY"], KEEP, ASCII_KEEP)
    na = next(c for c in clouds if c["agency"] == "NATIONAL_ASSEMBLY")
    assert na["label"] == "국회"
    assert AGENCY_LABELS == {"NATIONAL_ASSEMBLY": "국회",
                             "PATIENT_GROUP": "환자단체",
                             "MEDICAL_SOCIETY": "의료진"}


def test_news_by_keyword_links_real_articles():
    clouds = build_agency_clouds(_rows(), ["보건복지부", "NATIONAL_ASSEMBLY"], KEEP, ASCII_KEEP)
    mohw = clouds[0]
    arts = mohw["newsByKeyword"]["약가"]
    assert 1 <= len(arts) <= 5
    assert all("약가" in a["title"] or a["url"] for a in arts)
    assert arts[0]["source"] == "데일리팜"
    # naver_link 우선 (없으면 url)
    na = next(c for c in clouds if c["agency"] == "NATIONAL_ASSEMBLY")
    assert na["newsByKeyword"]["급여"][0]["url"] == "http://n/3"


def test_top_n_cap():
    from agents.gov_agency_clouds import _STOPWORDS
    # 유니크 한글 2글자 단어 40개 생성 (불용어 회피)
    syl = "가나다라마바사아자차카타파하거너더러머버서어저처커터퍼허고노도로모보소오조초"
    words = []
    for a in syl:
        for b in syl:
            w = a + b
            if w not in _STOPWORDS and w not in words:
                words.append(w)
            if len(words) == 40:
                break
        if len(words) == 40:
            break
    rows = [{"brand": "정책일반", "title": w, "description": "",
             "url": f"http://a/{i}", "naver_link": "", "source_name": "", "pub_date": "2026-07-05"}
            for i, w in enumerate(words)]
    clouds = build_agency_clouds(rows, ["정책일반"], frozenset(), frozenset(), top_n=30)
    assert len(clouds[0]["keywords"]) == 30
