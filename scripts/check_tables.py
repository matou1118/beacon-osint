import sqlite3
c = sqlite3.connect('data/beacon.db').cursor()
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
for r in c.fetchall():
    print(r[0])
print("---")
# Check source_schedule table
for table in ['source_schedule', 'sources', 'source_status']:
    try:
        c.execute(f"SELECT COUNT(*) FROM {table}")
        print(f"{table}: {c.fetchone()[0]} rows")
    except:
        print(f"{table}: does not exist")
