"""Fetch live TÜİK data (needs an SMS-verified API key).

    export TUIK_API_KEY=xxxxxx
    python examples/live_fetch.py

The key never leaves your machine except as a bearer token to TÜİK.
"""

from tuik_data import TuikClient

with TuikClient() as client:
    print(client.get_indicator("tufe_genel", last_n=3).to_csv(labels="both"))
