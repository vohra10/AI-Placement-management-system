
import sqlite3
from pathlib import Path

db_path = Path(__file__).resolve().parent / "placement.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

print("Database:", db_path)

rows = conn.execute("""
    SELECT a.id, a.student_id, a.job_id, a.status,
           u.name, u.email
    FROM applications AS a
    JOIN users AS u ON u.id = a.student_id
    WHERE LOWER(u.email) = LOWER(?)
""", ("stevensmith10@gmail.com",)).fetchall()

print("Steven's applications:")
for row in rows:
    print(dict(row))

conn.close()
