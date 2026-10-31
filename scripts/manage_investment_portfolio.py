"""Import explicit user-confirmed account events without creating inferred trades."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from screener.investment_v2.portfolio import PortfolioLedger


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', required=True, type=Path)
    parser.add_argument('--events', required=True, type=Path)
    args = parser.parse_args()
    events = json.loads(args.events.read_bytes())
    if not isinstance(events, list):
        parser.error('explicit JSON event list required')
    ledger = PortfolioLedger(args.ledger)
    for event in events:
        ledger.append(event)
    rows = ledger.events()
    print(json.dumps(dict(status='IMPORTED_USER_CONFIRMED_EVENTS', events=len(rows), head_hash=rows[-1]['hash'] if rows else None)))


if __name__ == '__main__':
    main()
