# 해외약가(A8) 업무 README

> 해외약가 조사·계산·업로드·정정을 누가 하든 **같은 자료원, 같은 가격 단계, 같은 산식**으로 하기 위한 기준 문서.
> 코드·루틴·사람 모두 이 문서를 따른다. 규칙을 바꾸면 이 문서, 코드, 테스트를 **함께** 바꾼다.
> 최종 개정 2026-11-02. 근거: 심평원 「약제의 요양급여대상여부 등의 평가기준 및 절차 등에 관한 규정」 [별첨5] 외국 조정가격 산출 기준 및 방법(2022.12.23 신설, 규정 제527호 2025.3.5 시행본 기준).

## 1. 범위와 대원칙

- **A8** = 미국·영국·독일·프랑스·이탈리아·스위스·일본·캐나다. 이 문서는 A8 조정가 기준이다. OECD·공단 추가 29개국은 계정 스킬 `nhis-foreign-drug-price`가 기준이며 A8 비율을 적용하지 않는다.
- **요청 기반 수집**: 사용자가 대시보드에서 요청한 제품·국가만 조사해 누적한다. 자동 백필·선제 재수집 금지. 미요청 국가의 미수집·노후 가격은 결함이 아니다.
- **틀린 값보다 빈 칸**: 0원, 추정가, 다른 가격 단계로의 대체 금지. 가격을 못 찾으면 `unconfirmed`(검색 0건·가격 공란·과거자료만) 또는 `failed`(접속·로그인·파싱 오류)로 사유와 함께 남긴다.
- **원문 그대로**: 현지 원포장 가격, 가격 단계, VAT, 포장 수량, 관찰일·시행일·자료 기준일을 따로 저장한다. KRW·조정가는 서버 계산기만 만든다(업로드 값은 무시).
- **서버 legacy live scraping API 호출 금지**. 운영 수집은 Aside 요청 큐 워커가 한다. 서버 스크레이퍼(`agents/scrapers/*`)는 수동 전용이며 같은 규칙을 따른다.

## 2. 조정가 산식 (별첨5)

```
조정가 = {[(공장도 출하가 × 환율) × (1 + 부가세 10%)] × (1 + 유통거래폭 8.69%)}   — 원 미만 절사
```
- 환율: 접수 전월 포함 최근 36개월 평균 매매기준율(KEB하나은행 기간평균, `agents/exchange_rate.py`).
- 유통거래폭: **8.69% 고정**. 저가약 10.41%(내복외용 475원·주사 4,750원 미만)는 **의도적으로 반영하지 않는다**(2026-10-26 사용자 결정 — 구현하지 말 것).
- 공장도 출하가는 아래 국가별 규칙(§3). 계산 결과 `adjusted_price_krw`는 **최소단위(정·바이알 1개) KRW**. 포장 가격은 `pack_count`로 나눈다.
- 구현: `PriceCalculator.calculate_adjusted_price` (`FACTORY_RATIO`, `SOURCE_OVERRIDES`, `exfactory_price=`, 독일 산식 `agents/price_adjustment.germany_exfactory_local`).

## 3. 국가별 기준 (자료원 · 가격 필드 · 공장도가 산출)

| 국가 | 1순위 자료원 | 업로드할 가격(local_price) | 공장도가 산출 | 대체(1순위 불가 시) |
|---|---|---|---|---|
| 미국 US | Micromedex RED BOOK | **WAC** (AWP 금지) | WAC × **0.74** | 없음 — 미확정 |
| 영국 UK | MIMS (NHS List Price) | NHS 정가 | × **0.73** | MIMS 권한 제한 시 NHSBSA dm+d "NHS Indicative Price"(VAT 제외, 출처 라벨 명시). BNF 는 영국 밖 403 |
| 독일 DE | Rote Liste (DocCheck 로그인) | **AVP (EB)/FB** (포장 가격, VAT 포함). EB/FB 칸이 비어 있는 포장만 **AVP/UVP 대체** — 대시보드·엑셀에 "AVP/UVP 대체" 표시 | 별도 산식 `[(AVP/1.19 − 8.35)/1.03 − 0.7]/1.0315 × 0.93` (부가세 19%, 약국마진 3%+€8.35, 도매마진 3.15%+€0.70, 환급률 공장도가의 7%) — 실효 비율 €100≈0.66, €1,000≈0.73, 고가≈0.735 | EB/FB 미표시 → AVP/UVP(대체 표시). 둘 다 없으면 미확정 |
| 프랑스 FR | **BdM_IT(Assurance Maladie) PFHT — 서버 수집**(2026-11-10 복구·관보 10건 일치 확인). 안 열리면 **관보(Journal officiel) 가격 공시 PFHT**(브라우저) | **PFHT**(세금 제외 공장도가). 병원 전용(liste en sus)은 관보 L.162-16-6 의 **UCD 별 tarif de responsabilité HT**(= Vidal "Prix et tarif de responsabilité (HT) par UCD") | PFHT × **0.77** | 공적 급여되나 공장도가 미확인: Vidal/BDPM 공중가 × **0.65** (`source_type="vidal"`) |
| 이탈리아 IT | AIFA TrovaNorme 결정문(DET PRES/UPR 등) | **Prezzo ex-factory (IVA esclusa)** | × **0.93** (공장도가에도 적용) | Classe A 공중가(Prezzo al pubblico)는 쓰지 않는다 — 미확정 |
| 스위스 CH | BAG Spezialitätenliste(SL) 공개 API | **FAP**(Fabrikabgabepreis, VAT 제외) | FAP × **0.73** | SL 에 약가가 없을 때만 Compendium 판매가 × **0.65** |
| 일본 JP | 후생노동성 약가기준 목록 | 단가(약국판매가) | × **0.79** | 없음 |
| 캐나다 CA | Ontario Drug Benefit Formulary·EAP / PMPRB (서버 수집) | 단가(공장도가) | × **0.81** | **온타리오 미등재(병원 투여 항암제 등)일 때만**: 퀘벡 **INESSS 장관 제출 의견서의 제조사 보장 판매가(prix de vente garanti)**, 교차 확인 **CDA-AMC 급여 심사 보고서 제출 가격(public list price)** × 0.81 — 화면·엑셀에 "INESSS·CDA-AMC 제조사 제출 정가 대체" 표시 (2026-11-10 사용자 결정) |

### 국가별 실무 메모
- **DE**: 상세 페이지 하단 `PACKUNGSANGABEN` 표 열은 `포장 | N | PZN | AVP (EB)/FB | AVP/UVP`. 포장별로 행을 만든다(28정·1바이알·2 KIT 등). 기준은 **AVP (EB)/FB**(2026-11-02 사용자 결정 — 10-26 의 AVP/UVP 기준을 뒤집음). AVP/UVP 는 `extra.avp_uvp_reference`로 보존하고, EB/FB 가 없는 포장에서만 가격으로 쓰며 `price_basis_warning`으로 화면에 표시한다. `Preisstand`는 자료 기준일이지 가격 시행일이 아니다(→ `extra.price_as_of`, `effective_date=null`). DocCheck 는 좌석·세션 서비스 — 조회 후 로그아웃. 이 PC 네트워크에서 DocCheck(195.82.66.x)가 막힌 적이 있다(2026-10-19) — 다른 사이트가 정상이면 DE 를 failed 로 사유 기록.
- **CA 대체 출처 (2026-11-10 사용자 결정)**: 온타리오 약가집(ODB)·EAP 에 없을 때(키트루다처럼 병원 투여 항암제는 온타리오 항암제 지원 프로그램 대상이라 가격 비공개) ① 가장 최근 **INESSS 「Avis à la ministre」** PDF(inesss.qc.ca)의 "Justesse du prix" 문단·단가표 — 각주상 *prix de vente garantis soumis par le fabricant* ② **CDA-AMC Reimbursement Review/Recommendation** "Submitted price" 로 같은 값인지 교차 확인 ③ 퀘벡 **RAMQ 「Liste des médicaments – Établissements」**(가격 칸 없음)로 등재·DIN 확인. 업로드: `price_tier=ex_factory`, `price_tier_original="Prix de vente garanti soumis (INESSS Avis YYYY-MM-DD)"`, `source_url`=INESSS 또는 CDA-AMC 문서, `effective_date`=문서 일자, `extra.ontario_checked`(ODB·EAP 검색 결과)·`extra.cda_amc_crosscheck`·`extra.ramq_din`. 서버가 `price_column=submitted_list_price` 와 대체 표시를 붙인다. 협상가(pCPA)·비공개 할인은 반영되지 않는다. 예: 키트루다 100 mg/4 mL 바이알 CAD 4,400 (INESSS 2026-08-27, CDA-AMC 2025-10). **심평원 실무에서 이 제출가를 캐나다 공장도가로 인정하는지는 미확인** — 레드팀 플래그로 남긴다.
- **FR (2026-11-10 사용자 결정)**: 1순위 **BdM_IT 서버 수집**(`server-collect REQUEST_ID FR`, `agents/foreign_sources/fr_bdm_it.py`). 약국 유통은 CIP 상세의 *Prix Fabricant HT*, 병원 전용은 UCD 상세의 **TAA MCO(liste en sus) Prix HT**(rétrocession 칸의 가격 없는 등록은 쓰지 않는다). **시행일 = Date d'application**(판매가 기준일·관보일은 extra). 검증: 관보 기준값 10건(CIP 9·UCD 1) 일치. 사이트가 안 열리면(서버 수집 오류) 아래 관보 브라우저 절차로 간다.
- **FR (관보 대체 경로)**: BdM_IT(`codage.ext.cnamts.fr`)는 2026-10 중순부터 파리 포함 전 세계에서 응답 없음(2026-10-27 외부 점검 13개국, ameli.fr 은 정상, 2026-01 에도 같은 장애). 같은 CEPS 공식 가격이 관보 「Avis relatif aux prix de spécialités pharmaceutiques」에 실린다. Légifrance 는 **브라우저로** 연다(스크립트 요청은 Cloudflare 403, 브라우저는 수 초 후 자동 통과). 검색: `https://www.legifrance.gouv.fr/search/jorf?tab_selection=jorf&searchField=ALL&query=<상표>&sortValue=PUBLI_DATE_DESC` → 가장 최근 "Avis relatif aux prix…". 공시 PPTTC 가 BDPM 현재 공중가와 같으면 그 공시가 현재 유효하다. 시행일은 공시 문구(`à compter du …`, `…jour suivant la publication`, `Date d'effet` 열)로 정한다. BDPM 파일에는 PFHT 가 없다. 공장도가는 고가약에서 공중가의 약 96%(마진 상한제).
- **IT**: TrovaNorme API `tnf-service/ricerca/?determinaTNFSource=true&parola=<상표>` → `determina/tnf/pubblicate/<id>` → `allegato/<id>` PDF. AIC별 최신 유효 결정문. Fly 서버에서 이 호스트 DNS 실패가 있었다(2026-10-26) — 브라우저 fetch 는 정상.
- **CH**: `GET https://epl.bag.admin.ch/api/sl/public/medicinal-products?search=<상표>&page=1&size=25` (page 는 1부터). 포장 수량은 `packSize`("2 Stk")에서 — 키트루다 100mg/4ml 은 SL 에서 **2바이알 1팩**. `lastPriceChange`가 가격 변경일. Compendium 은 Fly IP 를 간헐 차단.
- **US**: RED BOOK 은 브라우저 로그인 성공, 좌석 제한 — 로그아웃. `price_tier_original="WAC"`, `source_type="redbook_wac"`.
- **주사제**: 최소단위는 **바이알**. mL·농도로 바이알 수를 추론하지 않는다(`total_mg/unit_mg` 금지). 농도만 있고 용량이 없으면 per-vial 강도는 비운다.

## 4. 수집 흐름 (Aside 요청 큐 워커)

절차 상세: `docs/price_request_worker.md`. 10분 루틴 `대시보드 해외약가 요청 + Aside 원장 과제`가 수행한다.

1. `node scripts/price_request_client.mjs queue` → `claim` (lease, 5분 heartbeat). 한 번에 요청 1개, **국가는 1개국씩 순차**, DE 는 마지막.
2. 국가별 1순위 자료원을 브라우저로 열고 **증빙 캡처**(관찰시각 KST 포함)를 세션 tmp 에 저장.
3. 행 만들기 — DE·FR·CH 는 **행 생성기**를 쓴다(손으로 JSON 을 쓰지 않는다):
   ```sh
   # DE: Rote Liste 상세 본문 텍스트(innerText)를 파일로 저장한 뒤
   python3 scripts/a8_rows.py de --text de.txt --url <상세 URL> --product "<제품명>" --ingredient <INN> --form oral|injection --evidence <캡처> --out de.json
   # FR: 관보 공시 본문 텍스트(innerText)
   python3 scripts/a8_rows.py fr --text jo.txt --url <Légifrance URL> --ingredient <INN> --form oral|injection [--code <CIP13|UCD13> ...] --evidence <캡처> --out fr.json
   # CH: SL API 직접 조회
   python3 scripts/a8_rows.py ch --trademark <상표> --ingredient <INN> --form oral|injection [--strength "45 mg"] --evidence <캡처> --out ch.json
   ```
   생성 결과를 열어 제품·함량·포장·가격을 화면과 대조하고, 요청 범위 밖 규격은 지운다. `SKIP:` 메시지는 미확정 사유로 쓴다.
4. `upload REQUEST_ID CC --file country.json` → `readback` 으로 새 row id 확인.
5. 대시보드 국가 카드에서 가격·포장·출처·사유를 **화면으로** 확인(viewport 캡처, fullPage 금지) → `verify`.
6. `notify begin` → Gmail 발송 → 보낸편지함 message id 확인 → `notify sent`. 반송 주소로 재발송 금지.

## 5. 서버 업로드 검증 (`api/price_request_ingest.prepare_a8`)

업로드는 아래를 통과해야 저장된다(위반 시 400). 같은 규칙이 계산기 `source_type`을 정한다.

| 국가 | 검증 | 계산 |
|---|---|---|
| IT | `price_tier=ex_factory`, `vat_status=excluded` 필수 | × 0.93 |
| US | `price_tier_original=WAC`, `source_type=redbook_wac` 필수 | × 0.74 |
| DE | `price_tier_original` 에 **EB/FB** 표기(기준) 또는 UVP(대체 — `extra.avp_eb_fb_reference` 가 있으면 거부) | 별첨5 독일 산식. UVP 대체 행은 `price_basis_warning` |
| FR | `ex_factory` 이면 `excluded` 필수 → PFHT × 0.77. 그 외 단계는 공중가 대체 → × 0.65 | |
| CH | `ex_factory` 이면 `excluded` 필수 → FAP × 0.73. 판매가 + `extra.ex_factory_price` → FAP × 0.73. 판매가만 → × 0.65 | |

필수 필드는 `docs/price_request_worker.md` §3. `source_url`에 자격증명 금지, `observed_at`은 시간대 포함, `effective_date`는 미래 불가.

## 6. 품질 체크리스트 (업로드 전 매번)

- [ ] 제품·성분·제형·투여경로·함량이 요청과 일치 (IV ↔ SC, 정제 ↔ 과립 대체 금지)
- [ ] `local_price` = 해당 국가 1순위 가격 필드(§3), 원포장 기준, 현지통화 (DE 는 AVP (EB)/FB — 없을 때만 AVP/UVP 대체 표시)
- [ ] `pack_count` = 화면의 실제 정·바이알·포 수 (DE `28 Filmtbl.`, CH `2 Stk`, FR `B/28`·`2 flacons`)
- [ ] `price_tier`·`vat_status`·`price_tier_original` 이 원문과 일치
- [ ] 관찰일(`observed_at`) · 가격 시행일(`effective_date`) · 자료 기준일(Preisstand 등)을 섞지 않음
- [ ] 증빙 캡처와 원문 URL, 공시 번호(관보 JORF/NOR, AIFA 결정문 번호, PZN, GTIN, CIP)를 `extra`/`evidence_ref`에
- [ ] 0·추정·다른 단계 값 없음. 못 찾으면 `unconfirmed`/`failed` + 시도한 출처
- [ ] 대시보드 화면에서 새 행이 보이는지 확인한 뒤에만 verify·메일

## 7. 이미 저장된 행 정정 (운영 데이터 변경)

- 사전 승인 → **볼륨 스냅샷**(`flyctl volumes snapshots create vol_vjy1qzd09257m9mv`) → dry-run → 적용 → 서버 읽기 경로(`ForeignPriceAgent.get_cached_results`)로 확인.
- 원칙: 기존 행은 지우지 않는다. 가격 단계를 바꿀 때는 **새 행 추가**(최신 `searched_at`이 화면에 쓰이고 이전 행은 이력), 같은 단계의 메타 보강은 **compare-and-set 갱신**. 모든 스크립트는 undo JSON 을 DB 옆에 남긴다.
- 스크립트: `scripts/repair_ch_exfactory.py`(CH SL FAP·링크), `scripts/insert_de_avp_uvp.py`(DE 가격 열 교체 — EB/FB 또는 UVP 대체), `scripts/insert_it_tnf_exfactory.py`(IT 공장도가), `scripts/insert_fr_jo_pfht.py`(FR 관보 PFHT).
- 이미 발송한 메일·요청별 보관 스냅샷(Excel)은 바꾸지 않는다 — 필요하면 재요청.

## 8. 알려진 접근 이슈

| 출처 | 증상 | 대응 |
|---|---|---|
| BdM_IT (FR) | 2026-10 중순~11월 초 전 세계 접속 불가(2026-01 에도). 2026-11-10 복구 확인 | 안 열리면 관보 PFHT(브라우저) |
| Légifrance (FR) | curl·서버 요청 Cloudflare 403 | 브라우저로 연다. 비브라우저 공식 경로: DILA JORF 오픈데이터(일 단위 파일), Légifrance PISTE API(계정 필요) — 미구현 |
| Rote Liste / DocCheck (DE) | 이 PC 네트워크에서 DocCheck 차단 이력 | 다른 사이트 정상이면 DE failed + 사유 |
| MIMS (UK) | 계정 권한 "Other related role" — 모노그래프 제한 | dm+d NHS Indicative Price |
| TrovaNorme (IT) | Fly 서버 DNS 실패 이력 | 브라우저 fetch 로 API·PDF |
| Compendium (CH) | Fly IP 간헐 차단 | SL 공개 API 가 1순위라 영향 작음 |

## 9. 결정 이력

| 날짜 | 결정 |
|---|---|
| 2026-10-17 | 요청 기반 수집. 자동 백필 금지 |
| 2026-10-18 | 국가 1개씩 순차 조사(병렬 금지) |
| 2026-10-19 | UK dm+d 대체 출처 허용 |
| 2026-10-23 | CH 출처 링크 slug 를 제품별로(키트루다 고정 버그) |
| 2026-10-26 | 별첨5 원문 대조: CH SL FAP × 0.73(대체 Compendium × 0.65), DE AVP/UVP(→ 11-02 EB/FB 로 변경) + 독일 산식(0.6955 폐기), IT 공장도가 × 0.93(1.0 폐기, Classe A 공중가 제외) |
| 2026-10-26 | 저가약 유통거래폭 10.41% 반영 안 함 |
| 2026-11-02 | DE 기준을 **AVP (EB)/FB** 로 변경(EB 미표시 시 AVP/UVP 대체 + 화면 표시) |
| 2026-10-27 | FR: BdM_IT 접속 불가 → 관보 PFHT × 0.77, 공장도가 미확인 시 공중가 × 0.65. 행 생성기·업로드 검증 도입 |

| 2026-11-10 | CA: 온타리오 미등재 시 INESSS 의견서 보장 판매가(교차 CDA-AMC 제출가) × 0.81 대체, '제조사 제출 정가 대체' 표시. RAMQ 병원 목록은 등재 확인용(가격 칸 없음) |
| 2026-11-10 | 서버 수집 국가 = DE·CH·JP·US·CA·FR(BdM_IT, 시행일=적용일, 안 열리면 관보 브라우저). **UK·IT 는 브라우저 유지**(UK dm+d 서버 IP 403, IT TrovaNorme 서버 연결 반복 차단·결정문 PDF 사람 확인 필요). 서버 수집기는 한 번에 하나(US 좌석 제한) |

## 10. 열린 항목

- FR 관보 비브라우저 자동 수집(DILA/PISTE) 미구현 — 현재는 Aside 브라우저.
- 별첨5 ③ "최대포장 중 최고가 제품" 선택 규칙의 자동화 미구현 — 사람이 포장 선택 시 확인.
- 맥북 원본 DB 의 CH 시타글립틴 72행(포장 수량 1 저장) 점검 미완.
- 프리비미스 FR 주사, 이키르보 FR: 관보 가격 공시 미확인.

## 11. 파일 맵

| 역할 | 파일 |
|---|---|
| 산식·비율 | `agents/exchange_rate.py` (`PriceCalculator`), `agents/price_adjustment.py` (독일 산식) |
| 공용 파서 | `agents/foreign_sources/de_rote_liste_table.py`, `fr_jo.py`, `ch_sl.py` |
| 행 생성기 | `scripts/a8_rows.py` |
| 업로드 검증 | `api/price_request_ingest.py` (`prepare_a8`) |
| 읽기 재계산 | `agents/foreign_price_agent.py` (`get_cached_results`, `resolve_item_source_type`, `official_exfactory_price`, `is_it_public_price`) |
| 서버 스크레이퍼(수동) | `agents/scrapers/de_rote_liste.py`, `fr_bdpm.py`, `fr_vidal.py`, `it_aifa.py`, `it_aifa_tnf.py`, `ch_compendium.py`, `ch_sl.py` |
| 워커 절차 | `docs/price_request_worker.md`, `scripts/price_request_client.mjs` |
| 세부 규칙 | `agents/rules/foreign_agent_rules.md` |
| 회귀 테스트 | `tests/test_foreign_sources.py`, `test_de_formula_ratio.py`, `test_ch_sl_exfactory.py`, `test_foreign_factory_ratio_effective.py`, `test_foreign_factory_ratio_item_source.py`, `test_keytruda_injection_baseline.py`, `test_welireg_excel_baseline.py` |
