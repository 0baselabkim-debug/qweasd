"""GitHub metadata/CAS only; no market requests, cancellation or secret writes."""
import base64
import json
import os
import re
import subprocess
from urllib.parse import quote
from ..core import Failure, encoded
from . import policy


class APIError(Failure):
    def __init__(self,status):self.status=status;super().__init__('github_request_failed_'+str(status or 'transport'))


class GitHub:
    def request(self,path,method='GET',body=None):
        args=['gh','api',path,'--method',method,'--include']
        if body is not None:args+=['--input','-']
        p=subprocess.run(args,input=encoded(body) if body is not None else None,capture_output=True,timeout=60)
        match=re.search(rb'^HTTP/\S+ (\d+)',p.stdout,re.M);status=int(match[1]) if match else None
        if p.returncode:raise APIError(status)
        i=p.stdout.find(b'{');j=p.stdout.find(b'[')
        start=min(x for x in (i,j) if x>=0) if i>=0 or j>=0 else -1
        return json.loads(p.stdout[start:]) if start>=0 else {}

    def contents(self,path,ref=policy.STATE_BRANCH,repo=policy.REPO):
        value=self.request('repos/'+repo+'/contents/'+quote(path,safe='/')+'?ref='+quote(ref,safe=''))
        if value.get('encoding')!='base64':raise Failure('github_content_not_inline_base64')
        return json.loads(base64.b64decode(value['content'])),value['sha']

    def write(self,path,value,sha=None):
        raw=encoded(value)
        if len(raw)>900000:raise Failure('ledger_year_size_limit_requires_review')
        for key in ('GH_TOKEN','GITHUB_TOKEN','KIS_APP_KEY','KIS_APP_SECRET'):
            if os.getenv(key) and os.environ[key].encode() in raw:raise Failure('credential_in_github_ledger')
        body=dict(message='regular collector ledger: '+path,content=base64.b64encode(raw).decode(),branch=policy.STATE_BRANCH)
        if sha is not None:body['sha']=sha
        return self.request('repos/'+policy.REPO+'/contents/'+quote(path,safe='/'),'PUT',body)

    def run(self,run_id,repo=policy.REPO):return self.request('repos/'+repo+'/actions/runs/'+str(run_id))

    def active(self,repo=policy.REPO):
        result={}
        for status in policy.ACTIVE:
            page=1
            while True:
                data=self.request('repos/'+repo+'/actions/runs?status='+status+'&per_page=100&page='+str(page))
                if not isinstance(data.get('workflow_runs'),list):raise Failure('active_run_inventory_unverified')
                for r in data['workflow_runs']:result[str(r['id'])]=r
                if page*100>=data.get('total_count',10**9):break
                page+=1
                if page>10:raise Failure('active_run_inventory_too_large')
        return list(result.values())

    def workflow(self,run):
        v=self.request('repos/'+policy.REPO+'/contents/'+quote(run['path'],safe='/')+'?ref='+run['head_sha'])
        return base64.b64decode(v['content']).decode()


def check_runs(client,context):
    current=client.run(context['run_id'])
    if current.get('status')!='in_progress' or current.get('head_sha')!=context['code_commit']:raise Failure('current_run_not_live_or_code_mismatch')
    local=client.active();earlier=[];audit=[]
    for r in local:
        if str(r['id'])==context['run_id']:continue
        if r.get('path')==policy.WORKFLOW:
            if r.get('event')=='schedule':
                other=policy.freeze(r,policy.CRON)
                if other['asof']==context['asof'] and int(r['id'])<int(context['run_id']):earlier.append(str(r['id']))
            continue
        text=client.workflow(r)
        if 'KIS_REGULAR_APP_KEY' in text or 'KIS_REGULAR_APP_SECRET' in text:raise Failure('other_workflow_uses_regular_credentials')
        audit.append(dict(run_id=str(r['id']),path=r.get('path'),status=r.get('status'),scope='does_not_reference_regular_credential_names'))
    if earlier:raise Failure('earlier_active_regular_slot_owner')
    return dict(status='CURRENT_RUN_AND_LOCAL_QUEUE_RECHECKED',other_local_runs=audit)
