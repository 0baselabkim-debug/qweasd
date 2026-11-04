"""Account-independent daily producer routing. Missing control preserves qweasd."""
import datetime as dt
import json
from pathlib import Path
from ..core import Failure
from . import policy
from .github import APIError

Q='0baselabkim-debug/qweasd'
I='0baselabkim-debug/investment-integrated-dev'
PATH='routing/2030.json'
SCHEMA='regular-routing-v1'
CHECKS=('engine','dedicated_permission','github_access','lease_and_recovery','shared_slot','preservation','regression_native','actual_main_inspection','switch_safety','market_approval')


def inspect(client,asof,*,at=None):
    at=at or policy.utcnow();day=dt.date.fromisoformat(asof)
    try:record,revision=client.contents(PATH)
    except APIError as e:
        if e.status!=404:raise
        return dict(actor=Q,revision='ABSENT',kind='LEGACY_DEFAULT',record=None)
    if (record.get('schema')!=SCHEMA or record.get('status')!='GO' or record.get('actor') not in (Q,I)
            or record.get('slot')!='2030' or record.get('cron')!=policy.CRON):raise Failure('regular_routing_invalid_control')
    activated=dt.date.fromisoformat(record['effective_from']);decided=policy.timestamp(record['decided_at'])
    cutoff=dt.datetime.combine(activated,dt.time(19,30),policy.KST)
    if not decided<=cutoff or decided>at or not at<policy.timestamp(record['expires_at']):raise Failure('regular_routing_unreviewed_or_expired')
    if set(record.get('checks',{}))!=set(CHECKS) or any(v!='PASS' for v in record['checks'].values()):raise Failure('regular_routing_GO_checks_incomplete')
    if record.get('approval')!='USER_APPROVED_REGULAR_COLLECTION_CUTOVER_20261104' or record.get('market_use_approval')!='USER_APPROVED_KIS_REGULAR_FULL_5600_PER_GENERATION' or record.get('max_total_http')!=5600 or record.get('interval_seconds')!=1.05:raise Failure('regular_routing_scope_changed')
    if set(record.get('source_proofs',{}))!={Q,I} or any(not isinstance(h,str) or len(h)!=64 or set(h)-set('0123456789abcdef') for h in record['source_proofs'].values()):raise Failure('regular_routing_source_approvals_missing')
    return dict(actor=record['actor'] if day>=activated else Q,revision=revision,kind='REVIEWED_ROUTE',record=record)


def bind(client,c,actor,*,config=None):
    route=inspect(client,c['asof'])
    if config is not None and route['record'] is not None:
        if route['record']['source_proofs'][actor]!=config['native_proof_sha256']:raise Failure('regular_routing_local_source_approval_changed')
    c.update(source_repository=actor,routing_actor=route['actor'],routing_revision=route['revision'])
    return route['actor']==actor


def recheck(client,c,actor,*,config=None,config_path=None):
    route=inspect(client,c['asof'])
    if route['actor']!=actor or route['revision']!=c.get('routing_revision','ABSENT') or c.get('source_repository',Q)!=actor:
        raise Failure('regular_routing_changed_or_actor_not_selected')
    if route['record'] is not None and config_path is not None:config=json.loads(Path(config_path).read_bytes())
    if config is not None and route['record'] is not None and route['record']['source_proofs'][actor]!=config['native_proof_sha256']:
        raise Failure('regular_routing_local_source_approval_changed')
    return route
