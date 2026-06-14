import os
from pathlib import Path
import openpyxl

md_dir = Path("/Users/kimjeong-ae/MA_AI_Dossier/data/hira_pipeline/HIRA_보도자료")
excel_path = Path("/Users/kimjeong-ae/MA_AI_Dossier/data/hira_pipeline/hira_committee_master.xlsx")

# 1. Analyze Markdown files
md_files = list(md_dir.glob("*.md"))
print(f"Total markdown files on disk: {len(md_files)}")

drug_count = 0
unique_drugs = set()
for f in md_files:
    if "drug" in f.name:
        drug_count += 1
    else:
        # Extract drug name from filename
        parts = f.name.replace(".md", "").split("_")
        if len(parts) >= 4:
            unique_drugs.add("_".join(parts[3:]))

print(f"Files containing 'drug' in name: {drug_count}")
print(f"Files with specific drug names: {len(md_files) - drug_count}")
print(f"Unique specific drug names in filenames: {len(unique_drugs)}")
print(f"Sample of specific drug filenames:")
for f in sorted(list(md_files))[:20]:
    if "drug" not in f.name:
        print("  ", f.name)

# 2. Analyze Excel sheet
if excel_path.exists():
    wb = openpyxl.load_workbook(excel_path, read_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    header = rows[0]
    data_rows = rows[1:]
    print(f"\nTotal Excel data rows: {len(data_rows)}")
    
    excel_none_count = sum(1 for r in data_rows if r[3] is None or r[3] == "None")
    print(f"Excel rows where Product Name is None/empty: {excel_none_count}")
    print(f"Excel rows where Product Name is valid: {len(data_rows) - excel_none_count}")
    
    print("\nSample of valid Excel products:")
    valid_products = [r[3] for r in data_rows if r[3] and r[3] != "None"]
    for p in sorted(list(set(valid_products)))[:20]:
        print("  ", p)
