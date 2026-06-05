# HIRA D+1 Email Draft Template

## Subject template

```text
[AI_MAx_Report] 제{session_ordinal}차 {committee_short_kr} 후속 보고서_{session_date_dot}
```

Examples:
- `[AI_MAx_Report] 제6차 약제평가위원회 후속 보고서_2026.06.04`
- `[AI_MAx_Report] 제6차 중증(암)질환심의위원회 후속 보고서_2026.07.08`

## Body template

```markdown
*메일의 전체 내용은 AI 로 생성 되었습니다. 실제 사실과 다르거나 사용에 있어 신중한 해석이 필요할 수 있습니다.*

안녕하세요.
*{year}년 제{session_ordinal}차 {committee_full_kr}({session_date_kr}) 결과를 아래와 같이 보고드립니다.*
{opening_summary}

*1. 심의 결과 (HIRA 공식 용어 기준)*

{result_items}

*2. MSD 자산 영향 (모니터링 우선순위 기준)*

{msd_asset_impact}

*3. {next_session_date_kr} 제{next_session_ordinal}차 {next_committee_short_kr} 안건 후보*

{next_candidates}

*4. 정책 환경 변화*

{policy_context}

상세 내용은 *첨부 PDF*를 참고 부탁드립니다.
감사합니다.
```

## Placeholder definitions

| Placeholder | Description |
|---|---|
| `{committee_short_kr}` | 약평위 / 암질심 / 약제평가위원회 등 제목용 짧은 명칭 |
| `{committee_full_kr}` | 약제급여평가위원회 / 중증(암)질환심의위원회 |
| `{session_ordinal}` | 현재 차수 숫자 |
| `{session_date_dot}` | `YYYY.MM.DD` |
| `{session_date_kr}` | `YYYY년 M월 D일` |
| `{opening_summary}` | 1~2문장 회의 총평 |
| `{result_items}` | 약제별 번호 목록. HIRA 공식 용어 사용 강제 |
| `{msd_asset_impact}` | MSD 자산 직접 영향 요약 |
| `{next_session_date_kr}` | 다음 차수 날짜 |
| `{next_session_ordinal}` | 다음 차수 숫자 |
| `{next_committee_short_kr}` | 다음 차수 위원회 짧은 명칭 |
| `{next_candidates}` | 다음 차수 후보 bullet/paragraph |
| `{policy_context}` | 정책 환경 변화 요약 |

## Required result item format

```markdown
1. *{drug_name}* ({company}, {indication_short})
→ *{hira_official_result}*
{one_sentence_rationale}
```

Allowed HIRA official result terms:
- 약평위: `급여 적정성 있음`, `평가금액 이하 수용 시 적정`, `위험분담 확대 적정`, `재심의`
- 암질심: `급여기준 설정`, `급여기준 미설정`

## Style rules

- Keep the email concise. The PDF/report contains detail.
- Email body should not include brdBltNo, media idx, PR rule IDs, precision/recall, or internal system metadata.
- Use bold only for key headings, drug names, and official result terms.
- Keep the AI disclaimer at the top exactly or near-exactly.
- Avoid the phrase `조건부 통과`; prefer `평가금액 이하 수용 시 적정` and explain only if necessary.
