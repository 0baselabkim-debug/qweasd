"""Explicit session runner for old auxiliary scripts and reviewed inline jobs."""
import argparse
import json
import runpy
import sys
from pathlib import Path
from .runtime import configured
from .lease import Blocked

ALLOWED={
 'collect_daily_snapshot.py','collect_decision_prices.py','recover_liquidity.py',
 'collect_macro.py','collect_sector_support.py','probe_adjusted_consistency.py',
 'probe_daily_optimization.py','probe_market_scope.py','verify_flow_trends.py',
 'refresh_decision_prices.py','verify_calendar_rehearsal.py',
}

def main(argv=None,session_factory=configured):
    values=list(sys.argv[1:] if argv is None else argv)
    boundary=values.index('--') if '--' in values else len(values)
    parser=argparse.ArgumentParser();parser.add_argument('--config',default='.github/config/kis-shared-account.json')
    parser.add_argument('--script',required=True);args=parser.parse_args(values[:boundary])
    root=Path(__file__).resolve().parents[1];script=(root/args.script).resolve()
    if not script.is_relative_to(root/'scripts') or script.name not in ALLOWED:raise Blocked('legacy_entrypoint_not_reviewed')
    original=list(sys.argv);paths=list(sys.path)
    try:
        with session_factory(args.config):
            sys.argv=[str(script),*values[boundary+1:]];sys.path.insert(0,str(root/'scripts'))
            runpy.run_path(str(script),run_name='__main__')
    finally:sys.argv[:]=original;sys.path[:]=paths

if __name__=='__main__':main()
