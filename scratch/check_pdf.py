import sys
from pathlib import Path
import pypdf

files = [
    "/Users/kimjeong-ae/MA_AI_Dossier/DREC Raw/로테맥스점안현탁액0.5%_급여_11-10_Loteprednol_etabonat.pdf",
    "/Users/kimjeong-ae/MA_AI_Dossier/DREC Raw/보리나2.5%주사_비급여_08-02_Sodium Folinate_27.3.pdf"
]

for f in files:
    path = Path(f)
    print("File:", path.name)
    print("Exists:", path.exists())
    if path.exists():
        try:
            with open(path, "rb") as pdf_file:
                reader = pypdf.PdfReader(pdf_file)
                print("Pages:", len(reader.pages))
                txt = ""
                for page in reader.pages:
                    t = page.extract_text()
                    if t:
                        txt += t
                print("Text length extracted:", len(txt))
                if txt:
                    print("Sample:", txt[:200])
                else:
                    print("[Empty or Scanned PDF]")
        except Exception as e:
            print("Error reading PDF:", e)
    print("-" * 40)
