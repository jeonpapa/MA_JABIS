# Daily Mailing 구독 관리 탭 + Locked Source 화면 — 설계

**작성일**: 2026-08-03
**출처**: 수정 요청서 GitHub `jeonpapa/MA_JABIS#1` — [Daily Mailing] 구독 관리 탭 및 locked source 기반 화면 구성
**영역**: Daily Mailing 구독 UI(`daily-mailing/page.tsx`) + mail-subscription API/백엔드

---

## 1. 목표 (What)

Daily Mailing 구독 화면을 **"구독 설정" / "등록 서비스 관리"** 2탭으로 정리한다.
사용자는 **모니터링 스콥 + 발송 설정만** 선택하고, 발송 소스는 **고정(LOCKED, read-only)** 으로
표시한다. 학습 전용 입력(BioSpectator·포워드 이메일)은 **화면에 노출하지 않는다**(백엔드 학습
입력으로만 유지). 등록된 서비스는 **개별 서비스 단위**로 ON/OFF·삭제한다.

## 2. 현재 상태 (기반)

- 탭: `'new'`(새 설정) / `'saved'`(저장됨). `saved` 는 이미 active 토글 + 삭제 보유.
- 모니터링 스콥(키워드·브랜드·회사·정책·질환 프리셋) + 발송 설정(미디어·수신자·모드) 존재.
- 소스: `MEDIA_CATEGORIES` 체크박스(`selectedMedia`) + `customSources`(URL 추가) — **현재 선택 가능**.
- API `mailSubscriptions.ts`: list/create/update/delete/testSend + `active`, payload에 `media`·`custom_sources`.
- 백엔드 `api/server.py`: `mail_subscription` CRUD 는 `owner_email` 단위(개별 소유자별).
- 스케줄러: `agents/ingest/daily_mailing_scope_export.py:162` `WHERE active=1` — **active 게이팅 이미 충족**.
- 학습 입력: `agents/policy_intelligence_ingest.py`(포워드 KRPIA/MA 이메일) — 백엔드 전용, UI 무관.

## 3. 변경 설계

### 3.1 탭 재구성 (`frontend/src/pages/daily-mailing/page.tsx`)

| 탭 | key | 내용 |
|----|-----|------|
| **① 구독 설정** | `new` | 모니터링 스콥(5그룹) + 발송 설정(Daily/Weekly·시간·수신자·모드) + **소스 LOCKED 표시** |
| **② 등록 서비스 관리** | `saved` | 등록 서비스 **개별 단위** 리스트 + ON/OFF(active) + 삭제 |

- 탭 라벨만 "구독 설정"/"등록 서비스 관리"로 변경(내부 key `new`/`saved` 유지 — 변경 최소화).
- `savedSettings` 카운트 배지는 "등록 서비스 관리" 탭에 유지.

### 3.2 소스 LOCKED 표시 (구독 설정 탭)

- **선택 UI 제거**: `MEDIA_CATEGORIES` 체크박스, "전체 선택", `customSources` URL 추가/삭제 UI 삭제.
- 대신 **read-only 잠금 표시**: canonical 소스셋(현 `MEDIA_CATEGORIES` 전체)을 🔒 아이콘과 함께
  비활성(회색) 칩/목록으로 표시. 제목 "발송 소스 (고정)", 부제 "시스템이 관리하는 고정 소스 — 선택 불가".
- 관련 상태·핸들러 제거: `selectedMedia`, `toggleMedia`/전체선택 로직, `customSources`·URL 추가 핸들러.
- **Training-only 블록은 만들지 않는다**(요청자 지시: 화면 미표시). BioSpectator·포워드 이메일은
  소스 목록에도 포함하지 않는다.

### 3.3 저장 payload 단순화

- `handleSubmit` payload 에서 `media`/`customSources` 제거.
- 저장 검증 조건에서 `selectedMedia.length` 제거 → `scopeTermCount > 0 && emailList.length > 0` 만 확인.
- `mailSubscriptions.ts MailSubscriptionInput`: `media`/`custom_sources` 를 **optional** 로(하위호환),
  프론트는 전달하지 않음.

### 3.4 백엔드 — 고정 소스셋 주입 (`api/server.py` mail-subscription create/update)

- 다운스트림(scope export·hermes)이 구독의 `media` 를 소비하므로, **입력에 media/custom_sources 가
  없거나 비어도** 저장 시 canonical 기본값으로 채운다:
  - `DEFAULT_MAIL_SOURCES` 상수(전문지+일간지 등 현 `MEDIA_CATEGORIES` id 전체)를 백엔드에 정의.
  - create/update 시 `media = body.media or DEFAULT_MAIL_SOURCES`, `custom_sources = []` 로 정규화.
- 이렇게 하면 모든 구독이 동일 고정 소스셋을 사용(UI 잠금과 일치) + 기존 export/hermes 파이프라인 무변경.

### 3.5 스케줄러 active 게이팅 (요청 #6)

- `daily_mailing_scope_export.py` 가 이미 `WHERE active=1` → **변경 없음**. 스펙 검증 항목으로만 확인.
- 회귀 테스트로 "active=0 구독은 export 제외" 를 고정.

### 3.6 등록 서비스 관리 탭 (요청 #5)

- 현 `saved` 탭의 리스트 + 토글(`updateMailSubscription active`) + 삭제(`deleteMailSubscription`) 유지·정리.
- **개별 서비스 단위**: 각 카드 = 하나의 등록 서비스. 카드별 ON/OFF 스위치 + 삭제 버튼. 팀 집계/일괄 없음.
- 리스트 소스는 기존 `listMailSubscriptions()`(owner_email 단위) 그대로.

## 4. 컴포넌트 파일

| 파일 | 변경 |
|------|------|
| `frontend/src/pages/daily-mailing/page.tsx` | 탭 라벨, 소스 선택→locked 표시, media/customSources 상태·핸들러 제거, payload 단순화, 관리탭 개별 카드 정리 |
| `frontend/src/api/mailSubscriptions.ts` | `MailSubscriptionInput` media/custom_sources optional |
| `api/server.py` | `DEFAULT_MAIL_SOURCES` 상수 + create/update 시 media 기본값 주입 |
| `agents/ingest/daily_mailing_scope_export.py` | 변경 없음(active=1 확인용 테스트만) |

## 5. 하위 호환 · 엣지

- 기존 저장 구독(media 값 있음): 그대로 동작. 관리 탭 표시/토글/삭제 무관.
- 구버전 payload(media 포함) POST: 백엔드가 수용하되 정규화(고정셋으로 대체하지 않고 값 있으면 유지 —
  단 프론트는 더 이상 보내지 않음). **결정: 값이 오면 유지, 없으면 DEFAULT_MAIL_SOURCES** (관리 API 안정성).
- Training 입력 파이프라인(`policy_intelligence_ingest`)은 이 변경과 독립 — 손대지 않음.

## 6. 범위 밖 (YAGNI)

- 소스 관리 UI(관리자가 canonical 소스셋을 편집) — 상수로 고정, 후속 과제.
- 팀/멀티유저 집계 뷰 — 개별 서비스 단위만.
- Training 입력 화면 노출 — 명시적 제외.

## 7. 테스트

- 프론트: `npx tsc --noEmit` + `npm run build` 통과(소스 선택 제거 후 미사용 상태/핸들러 없음).
- 백엔드: create/update 시 media 미전달 → 저장 레코드 media==DEFAULT_MAIL_SOURCES, custom_sources==[].
- 백엔드: media 전달 시 그 값 유지(하위호환).
- 회귀: `daily_mailing_scope_export` 가 active=0 구독 제외(active=1만 export).
