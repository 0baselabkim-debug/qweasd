"""One CAS document per year: claim, append history and latest handoff atomically."""
import copy
from ..core import Failure
from .github import APIError
from . import policy

TERMINAL={'FAILED','BLOCKED','SKIPPED_CLOSED','SKIPPED_WEEKEND','COMPLETE_PROVISIONAL','ARTIFACT_UNCONFIRMED'}


class Ledger:
    def __init__(self,client,context):self.client,self.context=client,context;self.path='ledger/'+context['asof'][:4]+'.json'
    def load(self):
        marker,_=self.client.contents('FORMAT.json')
        if marker!=policy.FORMAT:raise Failure('regular_state_branch_format_mismatch')
        try:data,sha=self.client.contents(self.path)
        except APIError as e:
            if e.status!=404:raise
            return dict(schema=1,year=self.context['asof'][:4],slots={},latest_complete=None),None
        if data.get('schema')!=1 or data.get('year')!=self.context['asof'][:4] or not isinstance(data.get('slots'),dict):raise Failure('regular_ledger_corrupt')
        return data,sha
    @property
    def key(self):return self.context['asof']+'/2030'
    def current(self):return self.load()[0]['slots'].get(self.key)
    def claim(self):
        data,sha=self.load()
        if self.key in data['slots']:return False,data['slots'][self.key]
        record=dict(context=copy.deepcopy(self.context),status='CLAIMED',history=[dict(at=policy.utcnow().isoformat(),status='CLAIMED')])
        data['slots'][self.key]=record
        try:self.client.write(self.path,data,sha)
        except APIError as e:
            if e.status not in (409,422):raise
            winner=self.current()
            if winner:return False,winner
            raise Failure('regular_ledger_CAS_conflict_no_market_calls')
        return True,record
    def assert_owner(self):
        r=self.current()
        if not r or r['context']!=self.context or r['status'] in TERMINAL:raise Failure('regular_slot_ownership_lost_or_terminal')
        return r
    def transition(self,status,*,summary=None,reason=None,artifact=None):
        data,sha=self.load();old=data['slots'].get(self.key)
        if not old or old['context']!=self.context:raise Failure('regular_ledger_owner_mismatch')
        if old['status'] in TERMINAL:raise Failure('regular_ledger_terminal_cannot_reset')
        allowed={'CLAIMED':{'PREPARING','BLOCKED','FAILED','SKIPPED_WEEKEND'},
                 'PREPARING':{'COLLECTING','BLOCKED','FAILED','SKIPPED_CLOSED'},
                 'COLLECTING':{'INTERNAL_PASS','FAILED','BLOCKED'},
                 'INTERNAL_PASS':{'COMPLETE_PROVISIONAL','ARTIFACT_UNCONFIRMED','FAILED'}}
        if status not in allowed.get(old['status'],set()):raise Failure('regular_ledger_transition_invalid')
        r=copy.deepcopy(old);r['status']=status;r['history'].append(dict(at=policy.utcnow().isoformat(),status=status,reason=reason))
        if len(r['history'])>16:raise Failure('unexpected_ledger_transition_count')
        if summary is not None:r['summary']=summary
        if artifact is not None:r['artifact']=artifact
        if reason:r['reason']=reason
        if status=='COMPLETE_PROVISIONAL':
            if not summary or summary.get('internal_validation')!='PASS' or not artifact or not artifact.get('digest'):raise Failure('cannot_complete_without_verified_artifact_and_internal_PASS')
            handoff=dict(generation_id=self.context['generation_id'],asof=self.context['asof'],slot='2030',status=status,
                internal_validation='PASS',provider_finality='UNVERIFIED',official_price_status='PENDING_KRX',
                run_id=self.context['run_id'],code_commit=self.context['code_commit'],artifact=artifact,
                dashboard_publication='NOT_PUBLISHED',application_reflection='NOT_UPDATED')
            previous=data.get('latest_complete')
            if not previous or previous['asof']<handoff['asof']:data['latest_complete']=handoff
        data['slots'][self.key]=r;self.client.write(self.path,data,sha)
        return r
