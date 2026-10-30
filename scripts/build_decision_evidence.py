"""Offline evidence CLI. No install, collection, Secrets, network or publication."""
import argparse
import datetime as dt
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from screener.decision_evidence.build import build, verify_output


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',type=Path,default=Path('snapshot'))
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--knowledge-cutoff',default=None)
    parser.add_argument('--limit',type=int,default=20)
    parser.add_argument('--verify-only',action='store_true')
    parser.add_argument('--expected-manifest-sha256')
    parser.add_argument('--source-kind',choices=('published','collector-artifact'),default='published')
    parser.add_argument('--reference-directory',type=Path)
    args=parser.parse_args()
    if args.expected_manifest_sha256 and not args.verify_only:parser.error('--expected-manifest-sha256 requires --verify-only')
    result=verify_output(args.out,args.expected_manifest_sha256) if args.verify_only else build(args.snapshot,args.out,args.knowledge_cutoff or dt.datetime.now(dt.timezone.utc).isoformat(),args.limit,source_kind=args.source_kind,reference_directory=args.reference_directory)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
