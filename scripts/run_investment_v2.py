"""Manual independent V2 integration. No market collection, publication or orders."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from screener.investment_v2.pipeline import run, verify_bundle
from screener.investment_v2.engine import JudgmentEngine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--expected-manifest-sha256', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--context', type=Path)
    parser.add_argument('--portfolio', type=Path)
    parser.add_argument('--decision-at')
    parser.add_argument('--limit', type=int, default=20)
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--model')
    parser.add_argument('--allow-live-openai', action='store_true')
    parser.add_argument('--max-openai-calls', type=int, default=4)
    args = parser.parse_args()
    if args.verify_only:
        result = verify_bundle(args.out, args.expected_manifest_sha256)
    else:
        if not args.evidence:
            parser.error('--evidence is required')
        if args.allow_live_openai and not args.model:
            parser.error('live calls require explicit --model; keys are never CLI arguments')
        result = run(args.evidence, args.expected_manifest_sha256, args.out, decision_at=args.decision_at,
                     context_path=args.context, portfolio_directory=args.portfolio,
                     engine=JudgmentEngine(model=args.model, allow_live=args.allow_live_openai, max_calls=args.max_openai_calls), limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
