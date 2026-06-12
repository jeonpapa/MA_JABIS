import os
import re
import json
import glob
import zlib
import logging
from pathlib import Path
from datetime import datetime
import openpyxl
from openpyxl import Workbook
import pypdf
import olefile

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[1]

def load_env() -> None:
    env_path = BASE_DIR / "config" / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

load_env()

# ─────────────────────────────────────────────────────────────
# 1. HWP & PDF Text Extractors
# ─────────────────────────────────────────────────────────────
def extract_pdf_text(file_path: str) -> str:
    """PDF 파일에서 텍스트를 추출합니다."""
    text_list = []
    with open(file_path, "rb") as f:
        reader = pypdf.PdfReader(f)
        for i, page in enumerate(reader.pages):
            page_text = page.extract_text()
            if page_text:
                text_list.append(f"--- Page {i+1} ---\n{page_text}")
    return "\n\n".join(text_list)

def extract_hwp_text(file_path: str) -> str:
    """HWP 파일에서 한글 5.0 레코드 구조를 해석하여 본문 텍스트를 추출합니다."""
    try:
        ole = olefile.OleFileIO(file_path)
    except Exception as e:
        logger.error(f"Failed to open HWP OLE structure: {e}")
        return ""
    
    dirs = ole.listdir()
    
    # 1. 압축 여부 확인
    compressed = False
    if ['FileHeader'] in dirs:
        header_data = ole.openstream('FileHeader').read()
        if len(header_data) >= 40:
            attributes = int.from_bytes(header_data[36:40], byteorder='little')
            compressed = bool(attributes & 0x01)

    # 2. BodyText 섹션들 추출
    sections = [d for d in dirs if len(d) > 1 and d[0] == 'BodyText' and d[1].startswith('Section')]
    sections.sort(key=lambda x: int(x[1].replace('Section', '')) if x[1].replace('Section', '').isdigit() else 0)
    
    full_text = []
    
    for sec in sections:
        stream_data = ole.openstream(sec).read()
        if compressed:
            try:
                # raw deflate decompress (-15 windowBits)
                data = zlib.decompress(stream_data, -15)
            except Exception:
                try:
                    data = zlib.decompress(stream_data)
                except Exception:
                    data = stream_data
        else:
            data = stream_data
            
        offset = 0
        limit = len(data)
        
        while offset < limit:
            if offset + 4 > limit:
                break
            header = int.from_bytes(data[offset:offset+4], 'little')
            tag_id = header & 0x3FF
            size = (header >> 20) & 0xFFF
            offset += 4
            
            if size == 0xFFF:
                if offset + 4 > limit:
                    break
                size = int.from_bytes(data[offset:offset+4], 'little')
                offset += 4
                
            record_body = data[offset:offset+size]
            offset += size
            
            # HWPTAG_PARA_TEXT = 67
            if tag_id == 67:
                try:
                    text = record_body.decode('utf-16-le', errors='ignore')
                    # Inline control chars cleanup: 
                    # 1-31 (except 9, 10, 13) are Hancom control character codes.
                    cleaned_chars = []
                    for char in text:
                        code = ord(char)
                        if 1 <= code <= 31 and code not in (9, 10, 13):
                            continue
                        cleaned_chars.append(char)
                    
                    para_text = "".join(cleaned_chars).strip()
                    if para_text:
                        full_text.append(para_text)
                except Exception:
                    pass
                    
    raw_text = "\n\n".join(full_text)
    
    # 3. Hancom 고유 포맷 문자열 제거 (Hanja로 디코딩되는 tdse, mltb, hmpg 등)
    # 捤獥(tdse), 汤捯(tdco), 湰灧(nppg), 桤灧(hmpg), 氠瑢(mltb) 등
    garbage_pattern = r'[\u6364\u7365\u6c64\u6d6f\u6e70\u7067\u6864\u6c20\u7402]+'
    cleaned_text = re.sub(garbage_pattern, '', raw_text)
    
    return cleaned_text

# ─────────────────────────────────────────────────────────────
# 2. LLM Information Extraction
# ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """
당신은 한국 건강보험심사평가원(HIRA)의 약제요양급여 적정성 평가결과를 분석하는 전문 분석가입니다.
주어진 텍스트는 약평위(또는 암질심) 심의결과 공개 문서의 원본 텍스트입니다.

다음 요구사항에 맞춰 정보를 정확히 추출하여 JSON 형식으로만 응답해 주세요.

=== 추출 조건 및 주의사항 ===
1. **영업비밀 마킹 항목 스킵**: 텍스트에 "ㅇㅇㅇ", "ㅇㅇ", 또는 공란(blank), "***" 등으로 표기되어 마킹(블라인드) 처리된 항목(예: 비공개 가격, 환급률, 세부 재정액 등)은 절대로 임의로 추정하여 메우지 말고, 반드시 null 또는 빈 배열([])로 처리하십시오.
2. **치료라인(line_of_therapy)**: 암종/질환의 치료 단계를 의미하며, 1차 치료는 "1L", 2차 치료는 "2L", 3차 치료 이상은 "3L+"로 표준화하십시오. (예: 2차 이상 ➔ "2L", 단독 3차 ➔ "3L+")
3. **위원회 종류(committee)**: 약제급여평가위원회는 "YAKPYUNGWI", 암질환심의위원회는 "AMJILSIM"으로 구분하십시오.
4. **심의결과(review_result)**: 급여 적정성이 있다고 판단된 경우 "APPROVED", 평가금액 이하 수용 시 급여 적정성 있음(가중평균가 수용 등)은 "CONDITIONAL_APPROVED", 급여 적정성 없음/비급여는 "REJECTED"로 분류하십시오.
5. **비용효과성 트랙(reimbursement_track)**: 
   - 경제성평가(비용-효용분석 등) ➔ "CUA"
   - 경제성평가 자료제출 생략 ➔ "PE_WAIVER"
   - 대체약제 가중평균가 수용 ➔ "WAP_ACCEPT"
   - 비용최소화 분석 ➔ "cost_minimization"
6. **정책적/정부 요인(policy_drivers)**:
   - 식약처 신속심사/가속승인/GIFT 품목 ➔ "FAST_TRACK"
   - 약평위에서 혁신성 등을 인정받아 ICER를 완화/탄력 적용받음 ➔ "INNOVATION_PREMIUM"
   - 경제성평가 생략 대상 약제 ➔ "PE_WAIVER"
   - 원샷 세포/유전자 치료제 ➔ "ONE_SHOT_THERAPY"
7. **위험분담제 유형(rsa_types)**: 환급형 ➔ "REFUND", 총액제한형 ➔ "EXPENDITURE_CAP", 성과기반형 ➔ "OUTCOMES_BASED". 해당하는 유형을 배열에 담으십시오. 없을 시 [].
8. **날짜 포맷**: 모든 날짜는 YYYY-MM-DD 포맷으로 추출하십시오. 추출 불가능 시 null.
   - mfds_approval_date: 식약처 허가일
   - application_date: 결정 신청일
   - amjilsim_date: 암질심 심의일
   - session_date: 약평위 심의일
9. **임상 요약(clinical_summary)**: 핵심 임상연구명(trial_name) 및 핵심 유효성 지표(mPFS, mOS, ORR 등), 안전성 특이사항(safety_notes)을 추출하십시오.

=== 응답 JSON 포맷 ===
```json
{
  "title": "위원회 평가결과 공개 제목",
  "committee": "YAKPYUNGWI | AMJILSIM",
  "ordinal": 3,
  "session_date": "YYYY-MM-DD",
  "brand_name": "제품명",
  "generic_name": "성분명",
  "manufacturer": "제약사명",
  "disease_category": "Oncology | Orphan | General",
  "disease_name": "대상 질환명 (예: 제2형 당뇨병, 비소세포폐암)",
  "cancer_type": "Solid Tumor | Hematologic Malignancy | null",
  "line_of_therapy": "1L | 2L | 3L+ | null",
  "review_result": "APPROVED | CONDITIONAL_APPROVED | REJECTED",
  "reimbursement_track": "CUA | PE_WAIVER | WAP_ACCEPT | cost_minimization | null",
  "rsa_types": ["REFUND", "EXPENDITURE_CAP", "OUTCOMES_BASED"],
  "alternative_drugs": ["대체약물1", "대체약물2"],
  "clinical_summary": {
    "trial_name": "임상시험명",
    "mPFS": "중앙값 무진행생존기간 정보",
    "mOS": "중앙값 전체생존기간 정보",
    "ORR": "객관적 반응률 정보",
    "safety_notes": "주요 이상사례 요약"
  },
  "policy_drivers": ["FAST_TRACK", "PE_WAIVER", "ONE_SHOT_THERAPY", "INNOVATION_PREMIUM"],
  "mfds_approval_date": "YYYY-MM-DD",
  "application_date": "YYYY-MM-DD",
  "amjilsim_date": "YYYY-MM-DD"
}
```
"""

def extract_structured_data(text: str) -> dict:
    """텍스트에서 LLM을 호출하여 HIRA 평가 결과 데이터를 구조화합니다."""
    from openai import OpenAI
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise ValueError("OPENAI_API_KEY가 설정되지 않았습니다.")
    
    client = OpenAI(api_key=api_key)
    
    # 입력 텍스트가 너무 긴 경우 슬라이싱 (보통 평가결과서는 2만자 이내)
    input_text = text[:15000]
    
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": input_text}
        ],
        temperature=0.1,
        response_format={"type": "json_object"}
    )
    
    raw_content = response.choices[0].message.content
    return json.loads(raw_content)

# ─────────────────────────────────────────────────────────────
# 3. Output Generators (Markdown & Excel)
# ─────────────────────────────────────────────────────────────
def generate_markdown(data: dict, output_dir: Path) -> Path:
    """구조화된 데이터를 바탕으로 Obsidian 연동 Markdown 아카이브를 생성합니다."""
    date_str = data.get("session_date") or "unknown_date"
    committee_name = "약평위" if data.get("committee") == "YAKPYUNGWI" else "암질심"
    ordinal = f"{data.get('ordinal')}차" if data.get("ordinal") else "unknown차"
    brand_clean = re.sub(r"[^\w\s-]", "", data.get("brand_name") or "drug").strip().replace(" ", "_")
    
    filename = f"{date_str}_{committee_name}_{ordinal}_{brand_clean}.md"
    output_path = output_dir / filename
    
    # 날짜 파싱 및 lag 계산
    lag_str = "null"
    if data.get("session_date") and data.get("mfds_approval_date"):
        try:
            d_sess = datetime.strptime(data["session_date"], "%Y-%m-%d")
            d_appr = datetime.strptime(data["mfds_approval_date"], "%Y-%m-%d")
            lag_days = (d_sess - d_appr).days
            data["lag_days_approval_to_reimb"] = lag_days
            lag_str = str(lag_days)
        except Exception:
            data["lag_days_approval_to_reimb"] = None

    # YAML Frontmatter 작성
    yaml_lines = [
        "---",
        f'title: "{data.get("title")}"',
        f'brand_name: "{data.get("brand_name")}"',
        f'generic_name: "{data.get("generic_name")}"',
        f'manufacturer: "{data.get("manufacturer")}"',
        f'disease_category: "{data.get("disease_category")}"',
        f'disease_name: "{data.get("disease_name")}"',
        f'cancer_type: "{data.get("cancer_type") or "null"}"',
        f'line_of_therapy: "{data.get("line_of_therapy") or "null"}"',
        f'committee: "{data.get("committee")}"',
        f'session_date: "{data.get("session_date") or "null"}"',
        f'ordinal: {data.get("ordinal") or "null"}',
        f'review_result: "{data.get("review_result")}"',
        f'reimbursement_track: "{data.get("reimbursement_track") or "null"}"',
        f'rsa_types: {json.dumps(data.get("rsa_types", []))}',
        f'policy_drivers: {json.dumps(data.get("policy_drivers", []))}',
        f'mfds_approval_date: "{data.get("mfds_approval_date") or "null"}"',
        f'application_date: "{data.get("application_date") or "null"}"',
        f'amjilsim_date: "{data.get("amjilsim_date") or "null"}"',
        f'lag_days_approval_to_reimb: {lag_str}',
        "---",
        ""
    ]
    
    # 본문 줄글 및 테이블화 작성
    content = [
        f"# {data.get('title')}",
        "",
        "## 1. 기본 심의 정보",
        "",
        f"- **약제명**: [[{data.get('brand_name')}]] ({data.get('generic_name')})",
        f"- **제약사**: {data.get('manufacturer')}",
        f"- **적응증**: {data.get('disease_name')} ({data.get('line_of_therapy') or '치료단계 미상'})",
        f"- **심의 위원회**: {'약제급여평가위원회 (약평위)' if data.get('committee') == 'YAKPYUNGWI' else '암질환심의위원회 (암질심)'}",
        f"- **회의 차수**: 제 {data.get('ordinal')} 차",
        f"- **회의 일자**: {data.get('session_date')}",
        f"- **최종 심의결과**: **{data.get('review_result')}**",
        "",
        "## 2. 비용효과성 및 약가 전략",
        "",
        f"- **등재 평가 트랙**: `{data.get('reimbursement_track') or '미상'}`",
        f"- **위험분담제(RSA) 계약**: {', '.join(data.get('rsa_types', [])) if data.get('rsa_types') else '미적용'}",
        f"- **대체약제**: {', '.join(data.get('alternative_drugs', [])) if data.get('alternative_drugs') else '없음'}",
        f"- **정책적 요인 (Policy Drivers)**: {', '.join(data.get('policy_drivers', [])) if data.get('policy_drivers') else '없음'}",
        "",
        "## 3. 임상적 증거 및 유용성",
        "",
        f"- **주요 임상시험명**: {data.get('clinical_summary', {}).get('trial_name') or '미상'}",
        f"- **PFS (무진행생존기간)**: {data.get('clinical_summary', {}).get('mPFS') or '정보 없음'}",
        f"- **OS (전체생존기간)**: {data.get('clinical_summary', {}).get('mOS') or '정보 없음'}",
        f"- **ORR (반응률)**: {data.get('clinical_summary', {}).get('ORR') or '정보 없음'}",
        f"- **안전성 특이사항**: {data.get('clinical_summary', {}).get('safety_notes') or '정보 없음'}",
        "",
        "## 4. 관련 마일스톤 날짜 및 소요 일수",
        "",
        f"- **식약처 허가일**: {data.get('mfds_approval_date') or '미상'}",
        f"- **급여 신청일**: {data.get('application_date') or '미상'}",
        f"- **암질심 통과일**: {data.get('amjilsim_date') or '해당없음/미상'}",
        f"- **약평위 통과일**: {data.get('session_date') or '미상'}",
        f"- **허가 ➔ 약평위 소요 일수**: **{lag_str} 일**",
        ""
    ]
    
    full_md = "\n".join(yaml_lines + content)
    output_path.write_text(full_md, encoding="utf-8")
    logger.info(f"Generated Markdown: {output_path.name}")
    return output_path

def append_to_excel(data: dict, excel_path: Path) -> None:
    """구조화된 데이터를 Excel 마스터 시트에 누적 추가합니다."""
    # 엑셀 헤더 정의
    headers = [
        "회의 일자", "위원회", "차수", "제품명", "성분명", "제약사", "질환 카테고리", "질환명", 
        "암종 구분", "치료라인", "심의결과", "평가 트랙", "위험분담제(RSA)", "정책적 요인", 
        "식약처 허가일", "암질심 심의일", "소요 일수(허가➔약평위)"
    ]
    
    if not excel_path.exists():
        wb = Workbook()
        ws = wb.active
        ws.title = "약평위_심의마스터"
        ws.append(headers)
    else:
        wb = openpyxl.load_workbook(excel_path)
        ws = wb.active
        
    lag_days = data.get("lag_days_approval_to_reimb")
    row_data = [
        data.get("session_date"),
        "약평위" if data.get("committee") == "YAKPYUNGWI" else "암질심",
        data.get("ordinal"),
        data.get("brand_name"),
        data.get("generic_name"),
        data.get("manufacturer"),
        data.get("disease_category"),
        data.get("disease_name"),
        data.get("cancer_type"),
        data.get("line_of_therapy"),
        data.get("review_result"),
        data.get("reimbursement_track"),
        ", ".join(data.get("rsa_types", [])) if data.get("rsa_types") else "",
        ", ".join(data.get("policy_drivers", [])) if data.get("policy_drivers") else "",
        data.get("mfds_approval_date"),
        data.get("amjilsim_date"),
        lag_days if lag_days is not None else ""
    ]
    
    ws.append(row_data)
    wb.save(excel_path)
    logger.info(f"Appended row to Excel master: {data.get('brand_name')}")

# ─────────────────────────────────────────────────────────────
# 4. Main Batch Pipeline
# ─────────────────────────────────────────────────────────────
def process_batch(source_dir: Path, output_dir: Path, excel_path: Path, limit: int = None) -> None:
    """지정 디렉토리 하위의 PDF 및 HWP 결과 보고서들을 배치 처리합니다."""
    output_dir.mkdir(parents=True, exist_ok=True)
    excel_path.parent.mkdir(parents=True, exist_ok=True)
    
    # 1. 파일 목록 검색 (PDF, HWP)
    import unicodedata
    pdf_files = glob.glob(str(source_dir / "*.pdf"))
    hwp_files = glob.glob(str(source_dir / "*.hwp"))
    all_files = pdf_files + hwp_files
    
    logger.info(f"Found {len(all_files)} total files in {source_dir}")
    
    success_count = 0
    skip_count = 0
    error_count = 0
    
    # NFC 정규화 및 '평가결과' 필터링
    filtered_files = []
    for f in all_files:
        normalized_name = unicodedata.normalize('NFC', Path(f).name)
        if "회의자료" in normalized_name:
            skip_count += 1
            continue
        # 사용자 편의를 위해 '평가결과' 문구가 들어간 파일 우선 처리
        if "평가결과" in normalized_name:
            filtered_files.append(f)
        else:
            # 평가결과 문구가 없더라도 회의자료가 아니면 후보군에 포함
            filtered_files.append(f)
            
    logger.info(f"Filtered to {len(filtered_files)} potential target files (excluding '회의자료')")
    
    if limit is not None:
        # 주요 약제 10개를 선별하기 위해, 엔블로, 테빔브라, 트로델비, 킴리아 등 주요 키워드가 포함된 파일을 앞으로 정렬
        priority_keywords = ["엔블로", "테빔브라", "트로델비", "킴리아", "웰리렉", "브리디온", "키트루다", "옵디보", "티센트릭"]
        
        def get_priority(filepath):
            name = unicodedata.normalize('NFC', Path(filepath).name)
            for idx, kw in enumerate(priority_keywords):
                if kw in name:
                    return idx  # 우선순위가 높음 (0에 가까움)
            return len(priority_keywords)  # 기본 순위
            
        filtered_files.sort(key=get_priority)
        filtered_files = filtered_files[:limit]
        logger.info(f"Limiting execution to top {limit} priority files.")
        
    for file_path_str in filtered_files:
        path = Path(file_path_str)
        filename = path.name
        logger.info(f"Processing target file: {filename}")
        
        # 2. 텍스트 추출
        ext = path.suffix.lower()
        text = ""
        if ext == ".pdf":
            text = extract_pdf_text(str(path))
        elif ext == ".hwp":
            text = extract_hwp_text(str(path))
            
        if not text.strip():
            logger.warning(f"Extracted text is empty or failed for: {filename}")
            error_count += 1
            continue
            
        # 3. LLM 구조화
        try:
            structured_data = extract_structured_data(text)
            
            # 4. 아날로그 DB(Markdown) 및 Excel 저장
            generate_markdown(structured_data, output_dir)
            append_to_excel(structured_data, excel_path)
            success_count += 1
            
        except Exception as e:
            logger.error(f"Error processing {filename}: {e}")
            error_count += 1
            
    logger.info("=== Batch Processing Summary ===")
    logger.info(f"Success: {success_count} | Skipped (회의자료): {skip_count} | Errors/Failed: {error_count}")

if __name__ == "__main__":
    import sys
    
    source = BASE_DIR / "DREC Raw"
    out_dir = BASE_DIR / "data" / "hira_pipeline" / "HIRA_보도자료"
    excel_master = BASE_DIR / "data" / "hira_pipeline" / "hira_committee_master.xlsx"
    
    if len(sys.argv) > 1:
        # 단일 파일 수동 디버그 모드 또는 limit 모드
        arg = sys.argv[1]
        if arg.isdigit():
            process_batch(source, out_dir, excel_master, limit=int(arg))
        else:
            target = Path(arg)
            if target.exists() and "회의자료" not in target.name:
                ext = target.suffix.lower()
                text = extract_pdf_text(str(target)) if ext == ".pdf" else extract_hwp_text(str(target))
                data = extract_structured_data(text)
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                print("Target does not exist or is skipped (회의자료).")
    else:
        # 전체 배치 실행 (제한 없음)
        process_batch(source, out_dir, excel_master, limit=None)
