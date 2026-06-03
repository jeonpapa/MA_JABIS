---
id: DOC-agent-catalog
type: doc
tags: [doc, agent, catalog, reusable]
created: 2026-05-17
updated: 2026-05-17
---

# Agent Catalog — MA_Dossier 에이전트 전체 카탈로그

> 다른 프로젝트·도구로 옮겨 참조 가능한 도구 무관 문서.
> MA_Dossier 고유 경로는 `<PRJ>` 등 플레이스홀더로 표기 — 다른 시스템에 적용 시 경로만 치환.

심평원(HIRA) 약제급여 평가신청서 자동 작성 시스템의 에이전트 28종. 7개 그룹, Layer A(자료조사)→B(증거풀)→C(작성·평가) 아키텍처 기반.

---

## 0. 한눈에 — 7개 그룹

```
[오케스트레이션]  orchestrator
        ▼
[자료조사 Layer A]  research-discovery → paper-deep-read → paper-figure-extract
                    paper-dropzone-watcher (상시)
        ▼
[작성 Writer Layer C]  section-agent (본문) · drug-info-agent (바인딩) · econ-budget-agent
        ▼
[평가 Critic Layer C]  expert-critic → expert-repair → reference-benchmark
                       quality/style/domain/guideline checker · multi-llm-review
                       hira-reviewer · citation-check · document-reviewers
        ▼
[경제성 BIA]  bia-loop (+ compute-bia + bia-v2-engine)
        ▼
[양식 Builder]  hwpx-builder · apply_draft(python-hwpx)
        ▼
[보조]  asset-processor · training-loader · audit-trail-generator · session-manager
```

---

## 1. 오케스트레이션

### orchestrator
- **역할**: 전체 총괄. 사용자 요청 → GPT/Gemini 전략 토론 → 추천안 → CLI 컨펌 → 에이전트 순차 분배 → 결과 검증·보고
- **입력→출력**: `--task "..."` → session report JSON
- **모델**: Claude + GPT-4o + Gemini 병렬
- **특성**: 12-step pipeline 진입점. 멀티-LLM 합의로 전략 수립

---

## 2. 자료조사 (Researcher) — Layer A

| 에이전트 | 역할 | 입력→출력 | 모델/도구 |
|---------|------|----------|-----------|
| **research-discovery** | PubMed esearch + ClinicalTrials.gov + openFDA 검색 (검색만) | query → 신규 source 목록 | 공개 API |
| **paper-deep-read** | 논문 1편 deep-read → paper_context (design·N·endpoints·key_numbers·limitations·section_relevance) | msg-id → `evidence_pool/papers/<id>.md` | Gemini 2.5-pro |
| **paper-figure-extract** | PMC API·로컬 PDF에서 figure 추출 | → `evidence_pool/figures/` | pymupdf |
| **paper-dropzone-watcher** | `sources/_inbox/*.pdf` 상시 감시 → PMID 추출 → deep-read 자동 호출 | PDF drop → paper_context | chokidar + Gemini |

**공통 패턴**: paper_context는 abstract 스니펫이 아닌 full-context 요약. Layer B(Evidence Pool)에 축적, `index.json` 태그 검색.

---

## 3. 작성 (Writer / Draft) — Layer C

| 에이전트 | 담당 | 역할·특성 | 모델 |
|---------|------|----------|------|
| **section-agent** | 본문 섹션 (나·다·라·마) | rhetoric_profile + training_cases texture + paper_context full-text + 필드 앵커 → 섹션 markdown. 표 생성 지시, 필드별 역할 분담. **현행 주력** | Claude Opus |
| **drug-info-agent** | 기본정보 섹션 (가) | label → config 추출 → 필드 데이터 바인딩 (LLM 본문 생성 아님) | Gemini (추출) |
| **econ-budget-agent** | 경제성·재정 (바·자) | 수치 필드(플레이스홀더) / 서술 필드(Writer) / 식별자(바인딩) 3분류 | Claude |

**Writer 입력 5종 필수** (아키텍처 R1):
1. 섹션 evidence_bundle
2. 인용 논문들의 full paper_context
3. rhetoric_profile (정량 register)
4. training_cases texture 발췌
5. 이미 확정된 타 섹션 출력 (서사 일관성)

논리 에이전트 ID — 섹션별 1:1: `drug_info` / `disease_overview` / `textbook_guideline` / `publication_listing` / `clinical_trial` / `economic_evaluation` / `budget_impact`.

---

## 4. 평가·검증 (Critic / Validator) — Layer C 평가축

| 에이전트 | 평가축 | 비고 |
|---------|--------|------|
| **expert-critic** | 문장길이·수동태·헤지·인용밀도·연결어·프레임·앵커 보존 | 현재 stylistic만 (evidence·persuasion critic 미구현) |
| **expert-repair** | critic 피드백 기반 수리 루프 (최대 N회 증폭) | needs_minor_revision 수렴까지 |
| **reference-benchmark** | 통과본 대비 정량 11축 + 정성 5축 Likert | LLM judge는 Writer와 다른 모델 |
| **quality-checker** | 서사 일관성·PURPOSE 커버·confidence 임계 | — |
| **style-validator** | STYLE-RULE 준수 (서식·글꼴·여백·인용 형식) | 병렬 가능 |
| **domain-validator** | 섹션별 필수 요소·최소 인용·금지 콘텐츠 | 병렬 가능 |
| **writing-guideline-checker** | PII·번역·증거등급·문장길이 | **blocked 시 파이프라인 중단** |
| **multi-llm-review** | GPT-4o(규제) + Gemini(임상) 교차 리뷰 | source grounding + 임상 타당성 |
| **hira-reviewer** | 심평원 평가위원 관점 — 비교약제·임상근거·허가 일치·경제성 | — |
| **citation-check** | 본문 인용 ↔ evidence pool 대조 | 미매칭·미사용 논문 탐지 |
| **document-reviewers** | 문서 단위 리뷰 | — |

**3종 critic 합의 원칙** (R4): `stylistic + evidence + persuasion`.
- 통과 조건: stylistic=pass AND evidence=pass AND persuasion≥needs_minor
- citation 제거 방향 수리는 임상 증거 섹션에서 기본 거부
- 단일 critic 단독 판정 금지

---

## 5. 경제성·재정 (BIA)

| 에이전트/모듈 | 역할 |
|--------------|------|
| **bia-loop** | 단일 진입점 — economic_inputs → compute-bia → audit |
| **compute-bia + bia-v2-engine** | funnel walker·dual price·displacement·sensitivity 계산 엔진 (순수 함수) |
| **bia-narrative-agent** | BIA 결과 → 재정영향 섹션 markdown (미구현 — 예정) |

핵심: 동적 funnel(N단계), 표시가/실제가 분리, scenario_diff audit, 민감도 tornado.

---

## 6. 양식·산출 (Builder)

| 에이전트 | 역할 | 주의 |
|---------|------|------|
| **hwpx-builder** | 섹션 markdown → HWPX insertion plan | — |
| **apply_draft (python-hwpx)** | 셀 텍스트 주입 — 서식 손상 방지 권장 경로 | python-hwpx 라이브러리 |
| **apply-draft-text-to-form (직접 XML)** | ZIP-level 편집 (lineseg·height 룰) | 직접 편집 위험 — 최후 수단 |

HWPX 룰: `cellSz`·`sz height` 사용자 재계산 금지(앱 자동 reflow), lineseg `cpl = floor(horzsize/(vertsize×0.81))`.

---

## 7. 보조·인프라

| 에이전트 | 역할 |
|---------|------|
| **asset-processor** | 훈련 케이스 이미지·테이블 정규화 |
| **training-loader-v2** | training_cases → few-shot 예시 변환 (markdown+표+이미지) |
| **audit-trail-generator** | AI 생성 vs 인간 검토 추적 (ICH E6 / 21 CFR Part 11) |
| **session-manager** | 세션 로그·git 커밋·메모리 갱신 (외부 push는 보안 컨펌 필수) |

---

## 8. 전체 플로우 — 12-Step Pipeline

```
1. asset_process       asset-processor          (선택)
2. training_load       training-loader-v2       (선택)
3. draft               section-agent 등          (필수)
   ├ 4. quality_check
   ├ 5. style_validation     ┐
   ├ 6. domain_validation    ├ 병렬 (draft만 의존)
   ├ 7. guideline_check      │  → blocked 시 중단
   └ 8. multi_llm_review     ┘
9. hira_review
10. audit_trail
11. hwpx_build         (선택)
12. session_save
```

증거 기반 루프(별도):
```
Researcher(A) → Evidence Pool(B) → Writer↔Critic 증폭(C)
              → reference-benchmark (통과본 대비 위치)
```

---

## 9. 다른 프로젝트 적용 가이드

### 같은 도메인 (다른 약제)
- `projects/<PRJ>/` 폴더만 신규. 에이전트·룰·양식 그대로 재사용
- 약제별 고유: sources·evidence_pool·section_runs·form

### 다른 규제 도메인 (의료기기·식품 등)
재사용 vs 교체 구분:
| 재사용 (도메인 무관) | 교체 (도메인 고유) |
|---------------------|-------------------|
| `scripts/` 에이전트 엔진 | `writing_rules/*.json` 내용 |
| `lib/` 공통 모듈 | `training_cases/` 통과본 |
| 12-step pipeline 구조 | 양식 파일 |
| Critic·Benchmark 골격 | `agent_skill_packs` source_priority |
| 12-step·3-critic·Reference Benchmark 원칙 | 섹션 구성 |

→ `scripts` + `lib` + `docs/architecture-rules.md` + 본 카탈로그를 별도 framework 레포로 분리.

---

## 10. 현황 (2026-05-17)

| 상태 | 에이전트 |
|------|---------|
| ✅ 작동 | orchestrator, research-discovery, paper-deep-read/figure-extract, dropzone-watcher, section-agent, drug-info-agent, econ-budget-agent, expert-critic/repair, reference-benchmark, quality/style/domain/guideline checker, multi-llm-review, hira-reviewer, citation-check, document-reviewers, bia-loop, hwpx-builder, apply_draft, asset-processor, training-loader-v2, audit-trail-generator, session-manager |
| ⚠️ 구버전 | agent-llm (→section-agent), training-loader v1 (→v2) |
| ❌ 미구현 | evidence critic, persuasion critic (R4 3종 중 2종), bia-narrative-agent |

**최우선 갭**: critic이 stylistic 1종만 — evidence·persuasion critic 구현 시 reference-benchmark의 외부 judge 의존도를 낮추고 R4 3종 합의 완성.
