from pathlib import Path
import openpyxl
import re

excel_path = Path("/Users/kimjeong-ae/MA_AI_Dossier/data/hira_pipeline/hira_committee_master.xlsx")
wb = openpyxl.load_workbook(excel_path, read_only=True)
ws = wb.active
rows = list(ws.iter_rows(values_only=True))[1:]

expected_names = []
for r in rows:
    sess_date = r[0]
    comm = "약평위" if r[1] == "약평위" or r[1] == "YAKPYUNGWI" else "암질심"
    ordinal = f"{r[2]}차" if r[2] else "unknown차"
    brand = r[3]
    if brand and brand != "None":
        brand_clean = re.sub(r"[^\w\s-]", "", brand).strip().replace(" ", "_")
        expected_name = f"{sess_date}_{comm}_{ordinal}_{brand_clean}.md"
        expected_names.append(expected_name)

print("Total expected names:", len(expected_names))
print("Unique expected names:", len(set(expected_names)))
