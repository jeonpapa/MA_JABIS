# KR MFDS Approval Agent 규칙 — 식약처 변경이력 기반 *공식 승인일* 결정

> **구현체**:
> - `agents/hta_scrapers/kr_mfds.py`          — 허가사항(효능·효과) 현행 스냅샷
> - `agents/hta_scrapers/kr_mfds_history.py`   — 변경이력 XML 파서 + diff
> - `agents/hta_scrapers/kr_mfds_indication_mapper.py` — DB indication ↔ 변경이력 버전 매핑
> - `scripts/apply_mfds_official_dates.py`     — DB 반영 (approval_date / date_source / label_url)
>
> **DB**: `indications_by_agency` (agency='MFDS')
> **새 컬럼**: `date_source` TEXT  — `'mfds_official'` / `'unverified_estimate'`

---

## 0. 에이전트 역할

MFDS(식약처) 허가 단위 데이터에서 **"언제 이 적응증이 실제로 승인되었는지"** 를 버전 diff 로 확정한다. `_build_mfds` 는 현행 라벨만 보기 때문에 모든 적응증이 동일한 `permit_date`(최초 허가일) 로 추정되는 오류가 발생한다. 본 에이전트는 다음으로 그 오류를 교정한다.

1. `getItemChangeHistList?itemSeq=X&docType=EE` HTML 에서 각 `<tr>` 의 `data-docdata` 속성(HTML-escaped XML) 파싱 → `MFDSHistVersion` 리스트
2. 시간순 diff → *첫 등장 PARAGRAPH* 의 change_date = 해당 적응증 공식 승인일
3. DB indication 시그니처(disease + LoT + combo + biomarker) 와 버전별 세그먼트 매칭 → `approval_date` 교체 + `date_source='mfds_official'`

---

## 1. MFDS 변경이력 구조 (핵심 이해)

효능효과 변경이력은 버전별 DOC XML.

```xml
<DOC title="효능효과" type="EE">
  <SECTION title="">
    <ARTICLE title="흑색종">                     <!-- disease header -->
      <PARAGRAPH>1. 수술이 불가능하거나…</PARAGRAPH>
      <PARAGRAPH>2. 완전 절제술을 받은…보조요법(adjuvant) 치료</PARAGRAPH>
    </ARTICLE>
    <ARTICLE title="비소세포폐암">
      …
    </ARTICLE>
  </SECTION>
</DOC>
```

**두 가지 구조 regime 존재**:

| 기간 | 구조 |
|------|------|
| pre-2018 / 2021+ | `ARTICLE title="질환명"` (정상) |
| 2018-01-24 ~ 2020-08-27 | `ARTICLE title=""` + 질환 header 가 **단독 PARAGRAPH** 로 존재 (flat) |

flat regime 은 `_is_disease_header()` 휴리스틱으로 구분자 문단을 찾아 수동 segment.

---

## 2. "현재 요법" vs "적격성 조건" 구분 — ★ 최중요 규칙

MFDS 라벨은 한 문단 안에 neo/adj 를 **다른 맥락**으로 동시에 언급하는 경우가 많다.

| 유형 | 예문 | 의미 |
|------|------|------|
| **현재 요법 선언** | "…수술 후 보조요법**(adjuvant)** 치료로서 단독 요법" | 이 indication 이 adj 임 |
| **현재 요법 선언 (린파자 style)** | "…고위험 조기 유방암 성인 **환자의 수술 후 보조요법**" | 문장 말미 선언 |
| **적격성 조건 (prior therapy)** | "이전에 수술 전 보조요법 또는 수술 후 보조요법 조건에서…" | 과거 치료 경험 조건 — 이 indication 은 adj 가 아닐 수 있음 |
| **주변기 요법** | "…**수술 전 보조요법(neoadjuvant)**으로 …, 그리고 이어서 **수술 후 보조요법(adjuvant)**으로…" | peri |

### LayerSpec 규칙 (`LOT_KR`)

```python
"adjuvant":    LayerSpec(
    include_any=["수술 후 보조요법(adjuvant)", "환자의 수술 후 보조요법"],
    exclude=["(neoadjuvant)"]
)
"neoadjuvant": LayerSpec(
    include_any=["수술 전 보조요법(neoadjuvant)", "환자의 수술 전 보조요법"],
    exclude=["(adjuvant)"]
)
"perioperative": LayerSpec(
    include_all=["(neoadjuvant)", "(adjuvant)"]
)
```

**핵심**: "현재 요법" 은 *영문 괄호*("(adjuvant)") 또는 *문장 말미 "환자의 수술 후 보조요법"* 으로만 표기됨. 이 두 패턴 중 하나가 있어야 adj 로 인정. 단순 "수술 후 보조요법" 문자열 포함 여부로 판정하면 적격성 조건에 오매칭된다.

---

## 3. 2단계 매칭 알고리즘 (`_version_has_match`)

```
for version in versions (과거→현재):
    for (disease_title, body) in _disease_segments(version):
        if disease_layer 불만족: skip
        for sub in _split_sub_indications(body):        # "1. …", "2. …" 단위 split
            if all(sub_layers.matches(sub)):            # LoT / combo / biomarker 모두 충족
                return (version.change_date, excerpt)
```

- **segment-blob 매칭 금지**: peri 문단(neo+adj 동시) 을 adj 시그니처가 세그먼트 전체 blob 으로 매칭하면 오매칭 발생
- **반드시 숫자 단위 sub-indication 블록 단위로 평가**
- 숫자 없는 단일 indication (예: welireg VHL) 은 전체 body 가 하나의 block

---

## 4. 매칭 키워드 사전 (확장 가이드)

신규 약물/적응증 처리 시 아래 dict 를 먼저 확인.

| dict | 추가 조건 |
|------|-----------|
| `DISEASE_KR[disease_area]` | 라벨에 쓰이는 질환 한국어 표기 1개 이상 필수 (여러 개면 OR) |
| `LOT_KR[line_of_therapy]` | 신규 LoT 표현 발견 시 include_any 확장 (예: "이전의 전신 치료") |
| `COMBO_KR[combo]` | 병용약 한국어 표기 필수 (예: "엔포투맙") |
| `BIOMARKER_KR[biomarker_class]` | MFDS 라벨의 표기 그대로 ("CPS≥10", "TPS≥50") |

**금지**: `monotherapy`/`chemotherapy` 같은 비-변별적 표현을 sub_layer 로 추가 — 거의 모든 adj/1L 라벨이 포함해서 의미 없음.

---

## 5. DB 반영 (`apply_mfds_official_dates.py`)

```bash
# dry-run
python -m scripts.apply_mfds_official_dates --product keytruda

# 실제 반영 (4개 약물 전체)
python -m scripts.apply_mfds_official_dates --all --apply
```

- 매칭 성공: `approval_date ← official_date`, `date_source='mfds_official'`, `label_url ← getItemChangeHistInfo` URL, `raw_source` 에 confidence/excerpt JSON
- 매칭 실패: `date_source='unverified_estimate'` (기존 추정 승인일 유지)
- `date_source` 컬럼이 없으면 자동 `ALTER TABLE ADD COLUMN`

`confidence`:
- `high`: layer ≥ 3 매칭
- `medium`: layer 2
- `low`: layer 1
- `unmatched`: 매칭 실패

---

## 6. UI 표시 (`data/dashboard/approvals.html`)

`srcBadge(ag)` 가 `mfds_official` → ✓ 공식 (green), `unverified_estimate` → ⚠ 추정 (yellow) 뱃지 렌더. agency 라벨에 병기.

---

## 7. 자동화 현황

**자동화 완료**:

1. **itemSeq 자동 조회** — `kr_mfds.resolve_item_seq(product_slug, candidates=[...])` 가
   `MFDS_ITEM_SEQ` (하드코딩) → `data/db/mfds_item_seq_cache.json` (런타임 캐시) →
   `nedrug.searchDrug` 검색 순으로 resolve. 신규 product 는 첫 검색 성공 시 캐시에 누적.
2. **`_build_mfds` 내부 자동 호출** — `agents/foreign_approval/builders.py::_apply_mfds_official_dates`
   가 현행 라벨 upsert 직후 `apply_official_dates(product_slug)` 를 호출.
3. **DISEASE_KR 커버리지 자동 감지** — `apply_official_dates` 반환값에
   `missing_disease_kr` 포함. `_apply_mfds_official_dates` 가 warning 로그 + `res.errors`
   에 기록. QualityGuard 일일 리뷰도 `generate_suggestions` 에서 독립 점검.

**수동 필요 작업** (여전히 사람 판단 필수):

- 신규 disease_area 등장 시 라벨의 실제 한국어 표현 확인 후 `DISEASE_KR` 에 추가
  (자동 추론은 오·과잉 매칭 위험이 커서 보류)
- 신규 combo/biomarker 표기 확장 (`COMBO_KR`, `BIOMARKER_KR`)
- itemSeq 자동 조회가 실패한 신규 product (영문 slug 로 MFDS 검색 무응답 시)

---

## 8. 실수 기록 — 이 규칙이 왜 생겼는가

### 실수 1: segment-blob 매칭 (최초 구현)

**증상**: 키트루다 NSCLC `adjuvant` 가 **2023-12-19** 로 매칭. 실제 KEYNOTE-091 adj 단독 허가는 **2024-05-14**.

**원인**: disease 세그먼트 전체 문자열 blob 에 "수술 후 보조요법" 포함 여부만 확인. 2023-12-19 P5 (KEYNOTE-671 주변기요법) 는 한 문단에 neo + adj 를 **동시** 선언하는데, blob 레벨 매칭은 이 문단 안의 "수술 후 보조요법(adjuvant)" 에 adj 시그니처가 걸려 버림.

**교훈**: peri 문단 = "neo + adj 병기 레짐". 세그먼트 blob 이 아닌 **숫자 단위 sub-indication block** 으로 evaluate 해야 peri 와 adj-only 를 구분할 수 있다.

### 실수 2: 과도한 exclude (1차 수정)

**증상**: 위 실수를 고치려고 `adjuvant.exclude=["수술 전 보조요법", "neoadjuvant"]` 로 설정. Lynparza BC adj (2023-02-23) 가 미매칭으로 밀려남.

**원인**: Lynparza BC adj 원문 — "이전에 **수술 전 보조요법** 또는 수술 후 보조요법 조건에서 항암화학요법 치료경험이 있는 … 환자의 수술 후 보조요법". neo 언급은 **적격성 조건(prior therapy)** 일 뿐인데 exclude 가 무조건 거부.

**교훈**: MFDS 라벨은 동일 문단에 "현재 요법" 과 "과거 치료 이력 조건" 을 함께 기재한다. 단순 문자열 배제는 안 됨. **현재 요법 선언 패턴**(영문 괄호 또는 "환자의 수술 후 보조요법" 말미형) 으로만 include, **현재 요법 neo 괄호표기** 만 exclude 해야 한다.

### 실수 3: 키워드 누락으로 시그니처 0 매칭

**증상**: Lynparza BC / PAAD / mCRPC / DTC 가 disease_layer 자체가 비어 모든 버전 skip.

**원인**: `DISEASE_KR` 에 VHL / BC / PAAD / mCRPC / DTC 가 없었음.

**교훈**: 신규 product 추가 시 그 product 의 `indications_master.disease` 값을 먼저 추출하여 `DISEASE_KR` 커버리지 확인 → 누락 시 라벨에서 실제 표현 확인 후 추가. "dMMR" / "발현 비율≥1" / "이전의 치료" 같은 자잘한 변형 키워드도 동일 원칙.

---

## 9. 검증 baseline

수정 후 다음이 모두 만족되어야 한다:

```
keytruda_nsclc_adjuvant_resectable_mono       → 2024-05-14   (KEYNOTE-091)
keytruda_nsclc_perioperative_resectable_mono  → 2023-12-19   (KEYNOTE-671)
keytruda_tnbc_perioperative_mono              → 2022-07-13   (KEYNOTE-522)
keytruda_hnscc_perioperative_resectable_pdl1_1_mono → 2025-10-02 (KEYNOTE-689)
keytruda_mel_adjuvant_adjuvant_mono           → 2019-05-13   (KEYNOTE-054)
keytruda_rcc_adjuvant_adjuvant_mono           → 2022-08-22   (KEYNOTE-564)
lynparza_bc_adjuvant_adjuvant_brca_mut_mono   → 2023-02-23   (OlympiA)
welireg_vhl_mono                              → 2023-05-23
```

한 건이라도 날짜가 빗나가면 peri/adj/neo LayerSpec 또는 disease segmentation 을 의심한다.
