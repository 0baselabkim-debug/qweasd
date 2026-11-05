"""Evaluate a preserved decision against an anchored, already verified local bundle."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from screener.investment_v2.records import evaluate_saved_record


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--record', type=Path, required=True)
    p.add_argument('--record-sha256', required=True)
    p.add_argument('--future-input', type=Path, required=True)
    p.add_argument('--future-sha256', required=True)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    result = evaluate_saved_record(args.record, args.record_sha256, args.future_input, args.future_sha256, args.out)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
