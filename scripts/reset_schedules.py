import sqlite3
from datetime import datetime, timezone

conn = sqlite3.connect('data/beacon.db')
c = conn.cursor()

# Check current count
c.execute('SELECT COUNT(*) FROM announcements')
before = c.fetchone()[0]
print(f'Announcements before: {before}')

# Check source_sync_state structure
c.execute("PRAGMA table_info(source_sync_state)")
cols = [r[1] for r in c.fetchall()]
print(f'source_sync_state columns: {cols}')

# Reset all source schedules
now = datetime.now(timezone.utc).isoformat()
c.execute("UPDATE source_sync_state SET next_due_at = ?, consecutive_failures = 0, last_error = NULL", (now,))
conn.commit()

c.execute("SELECT COUNT(*) FROM source_sync_state")
print(f'Reset {c.fetchone()[0]} source schedules')

conn.close()
