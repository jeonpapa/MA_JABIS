# 급여 기준(심평원) 운영 반영 절차 — 2026-10-30

운영(Fly `ma-ai-dossier`)에 **급여 기준 화면 + API + 데이터**를 올리는 절차. 데이터는 이미지에 실은 시드를 부팅 때 적재한다.
(Aside 샌드박스에는 git·flyctl 이 없어 이 절차의 4·5단계는 **사용자 터미널**에서 실행한다.)

## 0. 먼저 알아둘 것 — 배포는 반드시 '이 작업 폴더'에서

- 로컬 브랜치 `ui/apple-design-p0-fixes` 는 GitHub `origin/main`(8b1b9b5, 10-17)보다 **10-27 커밋까지 앞서 있다**(5337cf7).
  운영은 지금까지 이 작업 폴더에서 `fly deploy` 로 올려 왔고, 10-27 Daily Mailing hermes 모드처럼 **커밋되지 않은 변경도 포함**된다.
- 따라서 `origin/main` 을 새로 clone/checkout 해서 배포하면 최근 변경이 **되돌아간다**. `fly deploy` 는 이 폴더에서만.
- GitHub push 는 별개 문제다(origin 이 오래돼 있어 push 전에 사람이 브랜치 정리 방침을 정할 것). 배포에는 push 가 필요 없다.

## 1. 이미 확인한 것 (Aside, 로컬 리허설)

| 확인 | 결과 |
|---|---|
| 운영 모양 DB(로컬 DB 복사본에서 급여 기준 테이블 10개 제거) 에 시드 dry-run → apply | 성공, 약 1초 |
| 기존 86개 테이블 행 수 | **변화 없음**, `PRAGMA integrity_check` ok |
| 같은 DB 에 `--apply --if-changed` 재실행 | 건너뜀(멱등) |
| 시드로 만든 DB vs 로컬 DB 에서 주요 질의 8종(질환 그룹·폐암·다발골수종·아토피·TNF 비교·텍피데라 …) 결과 | **해시 동일** |
| 골든 테스트 33개를 시드 적재 DB 로 실행 | 통과 |
| 시드 로더 안전장치 테스트 7개(dry-run 무변경, 기존 테이블 보존, manual 보존, 충돌 거부, 실패 롤백, 재적용 백업) | 통과 |
| 운영 `/api/health` | 200, `db_ok: true`, 스케줄러 정상 |

## 2. 무엇이 올라가는가

- 코드: `agents/hira_criteria/*`, `api/hira_criteria_routes.py`, `api/server.py`(블루프린트 등록 3줄, try/except),
  `frontend/src/pages/reimbursement-criteria/*`, `frontend/src/api/criteria.ts`, `Sidebar.tsx`, `tabVisibility.ts`, 라우터 설정.
- 시드: `seed/hira_criteria/hira_criteria_seed.sqlite.gz`(6.6MB) + `VERSION.json` — 급여 기준 **새 테이블 8개 + 메타 1개**만.
  (허가초과 요법은 급여가 아니라 시드·화면에서 제외. 로더가 쓰지 않는 빈 허가초과 테이블 2개를 함께 만든다.)
- 부팅: `scripts/start_production.sh` 가 `seed_hira_criteria.py --apply --if-changed` 실행(실패해도 부팅은 계속).
- 의존성 추가 없음(표준 라이브러리 + 기존 requirements).
- 운영 DB 의 **기존 테이블은 읽지도 쓰지도 않는다.** 적용은 한 트랜잭션이고 실패하면 전부 롤백된다.

## 3. 실행 전 체크 (사용자 터미널, 이 폴더에서)

```bash
cd ~/MA_AI_Dossier
python3 scripts/build_hira_criteria_seed.py --check      # "시드 = 로컬 DB" 이어야 함 (데이터 갱신했다면 먼저 build 다시)
python3 -m unittest tests.test_hira_criteria_golden tests.test_hira_criteria_search \
  tests.test_hira_criteria_onco tests.test_hira_criteria_drug tests.test_hira_criteria_seed   # 모두 OK
git status -sb                                           # 브랜치 ui/apple-design-p0-fixes 인지 확인
```

## 4. 배포

```bash
cd ~/MA_AI_Dossier
fly deploy -a ma-ai-dossier
```

- 이미지 빌드(프런트 `npm run build` 포함)가 실패하면 배포가 중단된다. 헬스체크(`/api/health`, 120초 유예)가 실패하면 Fly 가 깨진 배포를 받지 않는다.
- (선택) 로컬 기록용 커밋: `git add agents/hira_criteria api/hira_criteria_routes.py api/server.py frontend/src scripts/build_hira_criteria_seed.py scripts/seed_hira_criteria.py scripts/start_production.sh seed/hira_criteria tests/test_hira_criteria_*.py agents/rules/hira_criteria_rules.md docs/ops/HIRA_CRITERIA_DEPLOY.md AGENTS.md CLAUDE.md .gitignore && git commit -m "급여 기준 화면·API·시드"` — **push 는 하지 말 것**(0번 참고).

## 5. 배포 직후 확인

```bash
fly logs -a ma-ai-dossier | grep -i -E "hira criteria|seed_hira|start\]"      # 부팅 로그
fly ssh console -a ma-ai-dossier -C "python scripts/seed_hira_criteria.py --status"
#  → db_seed_version == seed_version, 8개 테이블 행 수: kcd_code 44846 / kcd_alias 78043 / onco_criteria_regimen 1234 /
#    onco_criteria_regimen_drug 3130 / drug_criteria_item 681 / criteria_section_kcd 723 / criteria_disease_kcd 4506 / hira_criteria_source 29
curl -s https://ma-ai-dossier.fly.dev/api/health                                   # status ok 유지
```

브라우저(로그인 후) 사이드바 **급여·정책 > 급여 기준**: ① "다발골수종 이식 불가" ② "백금 치료 후 재발한 난소암, BRCA 변이"
③ "만 8세 아토피 듀피젠트" ④ 비교 휴미라/레미케이드/심퍼니 ⑤ 약제 탭 "텍피데라" — 상단에 "항암 공고 제2026-232호 … 시행 예정분 포함".

## 6. 되돌리기

- **코드만**: `fly releases -a ma-ai-dossier` 로 직전 릴리스 확인 → `fly deploy -a ma-ai-dossier --image <직전 이미지>`.
  급여 기준 테이블은 다른 코드가 쓰지 않으므로 코드를 되돌리면 데이터는 아무 영향 없이 남는다.
- **데이터까지 제거**(처음 적용이었다면): `/app/data/hira_criteria/seed_apply_<시각>.undo.json` 의 `UNDO_DROP_if_first_apply` 에 적힌 테이블을 `DROP TABLE`.
  재적용(시드 버전이 바뀐 경우)이었다면 `/app/data/hira_criteria/backup/hira_criteria_before_<시각>.sqlite` 에 직전 테이블이 있다.
- 사람이 고친 질병코드(`criteria_disease_kcd.method='manual'`)는 재적용 때도 보존된다.

## 7. 앞으로 갱신할 때

1. 로컬: `pipeline onco|drug-update` → `check` → `apply` (규칙 문서 참고) → 골든 테스트.
2. `python3 scripts/build_hira_criteria_seed.py` (시드 재생성, 버전 바뀜) → 위 3~5 반복. 부팅 때 새 버전만 자동 적재된다.
3. 수동 점검만 하려면 `fly ssh console -C "python scripts/seed_hira_criteria.py"`(dry-run, 변경 없음).
