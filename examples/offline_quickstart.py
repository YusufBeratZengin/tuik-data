"""Work with a JSON-stat file downloaded from the TÜİK Data Portal - no API key.

Run: python examples/offline_quickstart.py
"""

from pathlib import Path

from tuik_data import TuikClient

HERE = Path(__file__).parent

dataset = TuikClient.load(HERE / "tufe_example.json")
print(dataset)
print()
print(dataset.to_csv(labels="both").splitlines()[0])
print(dataset.records(labels="both")[1])
print()
print("last period only:")
last = dataset.take("TIME_PERIOD", ["2024-03"])
for row in last.records(labels="both"):
    print(f"  {row['TIME_PERIOD']:<14} {row['COICOP']:<55} {row['value']}  status={row['status']!r}")
