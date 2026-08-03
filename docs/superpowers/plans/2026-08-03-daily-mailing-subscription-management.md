# Daily Mailing 구독 관리 탭 + Locked Source 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Daily Mailing 구독 화면을 "구독 설정 / 등록 서비스 관리" 2탭으로 정리하고, 발송 소스를 고정(LOCKED, read-only)으로 표시하며(선택 제거), 학습 입력은 화면 미표시, 등록 서비스는 개별 단위로 ON/OFF·삭제한다.

**Architecture:** 프론트는 소스 선택 상태·핸들러를 제거하고 canonical 소스셋을 잠금 표시. 저장 payload 에서 media/custom_sources 제거. 백엔드는 `DEFAULT_MAIL_SOURCES` 로 media 기본값을 주입해 기존 export/hermes 파이프라인을 무변경 유지. 스케줄러 active 게이팅은 이미 존재(`WHERE active=1`).

**Tech Stack:** React/TypeScript(Vite), Flask(`api/server.py`), pytest.

**Spec:** `docs/superpowers/specs/2026-08-03-daily-mailing-subscription-management-design.md`

---

## File Structure

- Modify: `api/server.py` — `DEFAULT_MAIL_SOURCES` 상수, `mail_sub_create` 에서 media 기본값 주입 + required 완화
- Modify: `tests/test_mail_subscription_sources.py` (신규) — media 미전달 시 기본값 주입 검증
- Modify: `frontend/src/api/mailSubscriptions.ts` — `MailSubscriptionInput.media` optional
- Modify: `frontend/src/pages/daily-mailing/page.tsx` — 탭 라벨, 소스 선택 제거→locked 표시, 상태·핸들러·payload·검증 정리

---

## Task 1: 백엔드 — DEFAULT_MAIL_SOURCES + create 기본값 주입

**Files:**
- Modify: `api/server.py`
- Test: `tests/test_mail_subscription_sources.py`

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_mail_subscription_sources.py`:
```python
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import api.server as s


def test_default_mail_sources_defined():
    # canonical 고정 소스셋이 정의돼 있고 대표 매체를 포함
    assert isinstance(s.DEFAULT_MAIL_SOURCES, list) and s.DEFAULT_MAIL_SOURCES
    for mid in ("medi", "yakup", "hankyung"):
        assert mid in s.DEFAULT_MAIL_SOURCES


def test_create_without_media_injects_default(monkeypatch):
    # _coerce 결과에 media 가 없어도 create 경로가 DEFAULT 로 채우는지 (fields 빌더 단위 검증)
    body = {"name": "svc", "keywords": ["약가"], "schedule": "Daily",
            "time": "06:00", "emails": ["a@b.com"]}
    result = s._coerce_mail_sub_input(body)
    fields = result[0] if isinstance(result, tuple) else result
    fields = s._with_default_sources(fields)   # 신규 헬퍼
    import json
    assert json.loads(fields["media_json"]) == s.DEFAULT_MAIL_SOURCES
    assert json.loads(fields["custom_sources_json"]) == []


def test_create_with_media_kept(monkeypatch):
    fields = s._coerce_mail_sub_input({"media": ["medi"]})
    fields = s._with_default_sources(fields)
    import json
    assert json.loads(fields["media_json"]) == ["medi"]
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_mail_subscription_sources.py -q`
Expected: FAIL — `AttributeError: module 'api.server' has no attribute 'DEFAULT_MAIL_SOURCES'`

- [ ] **Step 3: 구현**

(a) `_coerce_mail_sub_input` 정의 **직전**에 상수 + 헬퍼 추가:
```python
# Daily Mailing 고정 소스셋 (LOCKED) — 프론트 MEDIA_CATEGORIES 와 일치.
# 사용자는 소스를 선택하지 않는다; 모든 구독이 이 canonical 집합을 사용.
DEFAULT_MAIL_SOURCES = [
    "medi", "doctorsnews", "medigate", "yakup", "kpanews", "hitnews",
    "chosun", "joongang", "donga", "hani", "kyunghyang",
    "hankyung", "maeil", "edaily", "mt", "fnews",
    "ytn", "kbs", "mbc", "naver", "daum",
]


def _with_default_sources(fields: dict) -> dict:
    """media/custom_sources 가 비어있으면 고정 소스셋으로 채운다 (create 시).
    값이 이미 있으면(구버전 payload) 그대로 유지 — 하위호환."""
    import json as _json
    if not fields.get("media_json"):
        fields["media_json"] = _json.dumps(DEFAULT_MAIL_SOURCES, ensure_ascii=False)
    if "custom_sources_json" not in fields:
        fields["custom_sources_json"] = _json.dumps([], ensure_ascii=False)
    return fields
```

(b) `mail_sub_create` 에서 required 튜플의 `"media"` 제거 + 기본값 주입.
기존:
```python
    required = ("name", "keywords", "media", "schedule", "time", "emails")
```
교체:
```python
    required = ("name", "keywords", "schedule", "time", "emails")
```
그리고 `fields["owner_email"] = owner` **직전**(coerce 성공 후)에 추가:
```python
    fields = _with_default_sources(fields)
```
(변수명은 실제 코드의 coerce 결과 dict 이름에 맞춘다 — `fields`.)

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_mail_subscription_sources.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: 커밋**

```bash
git add api/server.py tests/test_mail_subscription_sources.py
git commit -m "feat(mailing): DEFAULT_MAIL_SOURCES 고정 소스셋 + create 기본값 주입"
```

---

## Task 2: 프론트 API — media optional

**Files:**
- Modify: `frontend/src/api/mailSubscriptions.ts`

- [ ] **Step 1: MailSubscriptionInput.media 를 optional 로**

`media: string[];` → `media?: string[];` (다른 필드 불변).

- [ ] **Step 2: 빌드 확인**

Run: `cd frontend && npx tsc --noEmit`
Expected: page.tsx 가 아직 media 를 참조하면 에러 — Task 3 에서 정리. 이 스텝만으로는 타입 에러가 없어야 함(선택화는 완화이므로). 에러 없으면 진행.

- [ ] **Step 3: 커밋**

```bash
git add frontend/src/api/mailSubscriptions.ts
git commit -m "feat(mailing): MailSubscriptionInput.media optional (소스 고정 대비)"
```

---

## Task 3: 프론트 UI — 탭 정리 + 소스 LOCKED + 상태/payload 정리

**Files:**
- Modify: `frontend/src/pages/daily-mailing/page.tsx`

이 태스크는 한 파일 내 여러 연관 편집이다. 각 스텝 후 최종적으로 `npx tsc --noEmit && npm run build` 로 검증(미사용 변수/핸들러 없어야 함).

- [ ] **Step 1: 탭 라벨 변경**

탭 정의 배열(`{ key:'new', label:... }`, `{ key:'saved', label:... }`)의 라벨을
`new` → **"구독 설정"**, `saved` → **"등록 서비스 관리"** 로 변경. 내부 key(`new`/`saved`)는 유지.
저장 성공 후 `setActiveTab('saved')` 등 key 참조는 불변.

- [ ] **Step 2: 소스 선택 UI → LOCKED read-only 표시로 교체**

"모니터링 미디어 — 어디서 찾을지" 섹션 전체(미디어 카테고리 체크박스 블록 + "선택된 미디어 N개"
+ customSources 추가/목록 UI, 대략 해당 섹션 `<div>` 하나)를 아래 read-only 표시로 교체:
```tsx
{/* 발송 소스 (고정) — 시스템이 관리, 선택 불가 */}
<div>
  <h3 className={`text-sm font-semibold ${textMain} mb-1 flex items-center gap-1.5`}>
    <i className="ri-lock-line text-xs"></i> 발송 소스 (고정)
  </h3>
  <p className={`${textSub} text-xs mb-3`}>시스템이 관리하는 고정 소스입니다 — 선택할 수 없습니다.</p>
  <div className="flex flex-wrap gap-1.5">
    {MEDIA_CATEGORIES.flatMap(cat => cat.items).map(item => (
      <span key={item.id}
        className={`text-xs px-2.5 py-1 rounded-full border ${isDark ? 'bg-[#161B27] text-[#8B9BB4] border-[#1E2530]' : 'bg-gray-50 text-gray-500 border-gray-200'} inline-flex items-center gap-1`}>
        <i className="ri-lock-line text-[10px] opacity-60"></i>{item.label}
      </span>
    ))}
  </div>
</div>
```
Training-only(BioSpectator·포워드 이메일) 블록은 **추가하지 않는다**.

- [ ] **Step 3: 소스 관련 상태·핸들러 제거**

제거 대상:
- state: `selectedMedia`, `setSelectedMedia`(line ~89), `customSources`/`setCustomSources`(~98),
  `customSourceUrl`/`setCustomSourceUrl`(~99).
- 핸들러: 미디어 토글/전체선택 함수(`toggleMedia`, 카테고리 전체선택 로직 ~155),
  커스텀 소스 추가/삭제 함수(~184-198).
- 편집 복원부(`setCustomSources(setting.custom_sources ?? [])` ~235, 미디어 복원이 있으면 함께) 제거.

- [ ] **Step 4: payload·검증에서 소스 제거**

- `handleSubmit` payload 에서 `media: selectedMedia,` 와 `customSources,` 줄 제거.
- 저장 검증(line ~312): `if (scopeTermCount === 0 || selectedMedia.length === 0 || emailList.length === 0) return;`
  → `if (scopeTermCount === 0 || emailList.length === 0) return;`
- 요약/버튼의 `selectedMedia.length` 참조(~740 요약 배지의 "미디어 N개", ~751 submit disabled 조건) 제거/수정:
  - 배지 "미디어 N개" 항목 삭제.
  - submit disabled: `disabled={submitting || scopeTermCount === 0 || emailList.length === 0}`.

- [ ] **Step 5: 등록 서비스 관리 탭 — 개별 서비스 카드 유지**

`saved` 탭 렌더는 유지(개별 카드별 ON/OFF 토글 + 삭제). 저장 소스 표시(`setting.media` 매핑, ~840)는
그대로 두거나(참고 표시) 간결히 "고정 소스" 문구로 대체 가능 — **동작 변경 없음**. 팀 집계/일괄 없음 확인.

- [ ] **Step 6: 타입체크 + 빌드**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: 성공 (미사용 변수/핸들러 없음, media 참조 잔존 없음)

- [ ] **Step 7: 커밋**

```bash
git add frontend/src/pages/daily-mailing/page.tsx
git commit -m "feat(mailing): 구독설정/등록서비스관리 2탭 + 소스 LOCKED 표시(선택 제거)"
```

---

## Task 4: 통합 검증

- [ ] **Step 1: 백엔드 테스트 + active 게이팅 확인**

Run: `.venv/bin/python -m pytest tests/test_mail_subscription_sources.py -q`
Expected: PASS
Run: `grep -n "WHERE active=1" agents/ingest/daily_mailing_scope_export.py`
Expected: 스케줄러가 active=1 만 export (요청 #6 충족 — 코드 변경 없음 확인)

- [ ] **Step 2: 프론트 최종 빌드**

Run: `cd frontend && npm run build`
Expected: 성공

- [ ] **Step 3: 배포 대기 안내**

배포는 사용자 직접(`flyctl deploy`). 배포 후: 구독 설정 탭에 소스가 🔒 고정 표시, 저장 시 media 미전달
→ 백엔드 DEFAULT 주입, 등록 서비스 관리 탭에서 개별 ON/OFF·삭제 확인.

---

## Self-Review 결과

- **Spec 커버리지**: 탭 재구성(§3.1)→T3S1·T3S5, 소스 LOCKED(§3.2)→T3S2·S3·S4, payload 단순화(§3.3)→T2·T3S4,
  백엔드 고정 소스셋(§3.4)→T1, active 게이팅(§3.5)→T4S1, 관리탭 개별(§3.6)→T3S5. 모두 매핑됨.
- **타입 일관성**: `DEFAULT_MAIL_SOURCES`(백엔드)·`MEDIA_CATEGORIES`(프론트 표시) id 동일. `media` optional 로 프론트 미전달 대응.
- **플레이스홀더 없음**: 백엔드 완전 코드 포함. 프론트는 파일 내 편집 앵커 명시.
- **하위호환**: media 값 오면 유지(`_with_default_sources`), 없으면 DEFAULT. 기존 구독·update 무영향.
