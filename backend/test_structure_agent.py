import sqlite3

from src.structure import analyze


db = sqlite3.connect("market_data.db")
db.row_factory = sqlite3.Row

rows = db.execute(
    """
    SELECT ts, open, high, low, close, volume
    FROM candles
    WHERE symbol = ?
      AND timeframe = ?
    ORDER BY ts DESC
    LIMIT 500
    """,
    ("BTC_USDT", "15m"),
).fetchall()

candles = [dict(row) for row in reversed(rows)]

result = analyze(candles, "15m")

print("AGENT:", result.agent)
print("DIRECTION:", result.direction)
print("CONFIDENCE:", result.confidence)
print("STRENGTH:", result.strength)
print("VALID:", result.valid)

print()
print("EVIDENCE:")

for item in result.evidence:
    print("-", item)

print()
print("KEY LEVELS:")

for level in result.key_levels:
    print("-", level)