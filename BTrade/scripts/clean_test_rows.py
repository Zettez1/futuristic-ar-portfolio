import sqlite3

conn = sqlite3.connect("db/trades.db")
n = conn.execute("DELETE FROM trades WHERE strategy='test'").rowcount
conn.commit()
print("deleted test rows:", n)
conn.close()
