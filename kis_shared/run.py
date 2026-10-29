"""Run an existing synchronous entrypoint inside one exclusive session.

This command cannot enable a disabled rollout or provision GitHub credentials.
"""
import argparse
import json
import runpy
import sys
from .runtime import configured
from .lease import Blocked


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', required=True)
    p.add_argument('--module', required=True)
    p.add_argument('args', nargs=argparse.REMAINDER)
    args = p.parse_args()
    allowed = ('collector_v1.regular.runner', 'scripts.browser_trial', 'scripts.minimal_trial',
               'scripts.actions_trial', 'stage3.live_probe')
    if args.module not in allowed:
        raise Blocked('shared_entrypoint_not_allowed')
    try:
        admitted = False
        with configured(args.config):
            admitted = True
            sys.argv = [args.module]+args.args
            try:
                runpy.run_module(args.module, run_name='__main__')
            except SystemExit as error:
                if error.code not in (0, None):
                    raise
        return 0
    except Blocked as error:
        print(json.dumps(dict(status='BLOCKED', reason=str(error), market_http=None if admitted else 0,
                              note='No session admitted; inspect request ledgers if an entrypoint had started.')))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
