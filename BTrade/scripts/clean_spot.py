import sqlite3

ACTIVE = ("SHIB/USDT:USDT", "WLD/USDT:USDT", "TIA/USDT:USDT", "OP/USDT:USDT", "INJ/USDT:USDT", "WIF/USDT:USDT")
conn = sqlite3.connect("db/trades.db")
n = conn.execute("DELETE FROM trades WHERE closed_at IS NULL AND symbol NOT IN (%s)" % ",".join("?" * len(ACTIVE)), ACTIVE).rowcount
conn.commit()
print("closed stale open positions:", n)
conn.close()
