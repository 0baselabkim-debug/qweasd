"""Operator-controlled account drain. Never auto-releases on time or expiry.

Current runners keep their checked-out source. A collector may start only after
Actions proves every pre-fence runner has finished. This gate protects all new
formal AND bounded bootstrap HTTP paths, without interrupting an open response.
"""
import json
from pathlib import Path
from .lease import Blocked

CONTROL = Path(__file__).resolve().parents[1]/'config/kis-account-exclusive.json'


def check_live_admission(path=None):
    try:
        value=json.loads(Path(path or CONTROL).read_bytes())
        if value.get('schema')!=1 or not value.get('request_id'):
            raise ValueError()
        if value.get('state')=='ACTIVE':
            raise Blocked('account_exclusive_collection_active_no_new_monitor_http')
        if value.get('state')!='RELEASED':
            raise ValueError()
        proof=value.get('release_proof',{})
        if (proof.get('repository')!='0baselabkim-debug/qweasd'
                or not str(proof.get('run_id','')).isdigit()
                or proof.get('status')!='completed'
                or proof.get('all_http_resolved') is not True
                or proof.get('central_owner_released') is not True):
            raise ValueError()
        return value
    except Blocked:
        raise
    except (OSError, ValueError, TypeError):
        raise Blocked('account_exclusive_admission_state_unavailable') from None
