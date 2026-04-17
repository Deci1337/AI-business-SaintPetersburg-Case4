import csv
import os
from datetime import datetime

LOG_FILE = "data/dialogs.csv"


def log_dialog(user_id: str, query: str, answer: str, escalated: bool):
    is_new = not os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if is_new:
            w.writerow(["timestamp", "user_id", "query", "answer", "escalated"])
        w.writerow([datetime.now().isoformat(), user_id, query[:500], answer[:300], escalated])
