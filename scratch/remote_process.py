import json
import logging
import traceback
from pathlib import Path
from agents.backfill_agent import BackfillAgent

logging.basicConfig(level=logging.INFO)

try:
    config = json.loads(Path("config/settings.json").read_text("utf-8"))
    bf = BackfillAgent(config, Path("."))

    print("Resetting 2026.07.01 in download_log...")
    with bf.db._connect() as conn:
        conn.execute("UPDATE download_log SET process_status='pending' WHERE apply_date='2026.07.01'")
        conn.commit()
    print("Reset completed.")

    print("Processing existing files...")
    bf.process_existing_files()
    print("Process finished successfully.")
except Exception as e:
    traceback.print_exc()
