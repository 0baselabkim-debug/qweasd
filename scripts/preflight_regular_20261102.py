"""Finite read-only regular-collector audit; no KIS credentials or provider HTTP.

The Actions expressions provide secret EXISTENCE BOOLEANS, never secret values.
GitHub access is GET-only. This does not claim a regular slot or activate a job.
"""
import argparse
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from collector_v1.regular import policy
from kis_shared.lease import validate as validate_lease

REPO='0baselabkim-debug/qweasd'
TARGET='2026-11-02'
CONTROL=Path('.github/readiness/regular-20261102.json')
PROVIDER_ENV=('KIS_APP_KEY','KIS_APP_SECRET','KIS_ACCESS_TOKEN','KRX_API_KEY','DART_API_KEY','ECOS_API_KEY','FRED_API_KEY','OPENAI_API_KEY')


def digest(raw):return hashlib.sha256(raw).hexdigest()


def github_get(path):
    if not path.startswith('repos/'+REPO+'/') or '?' in path.split('/')[0] or any(x in path for x in ('..','\\','secrets','dispatch','cancel')):
        raise ValueError('read_only_GitHub_destination_rejected')
    result=subprocess.run(['gh','api',path,'--method','GET'],capture_output=True,timeout=35)
    if result.returncode:raise ValueError('read_only_GitHub_metadata_unavailable')
    return json.loads(result.stdout)


def contents(path,ref):
    obj=github_get('repos/'+REPO+'/contents/'+path+'?ref='+ref)
    if obj.get('encoding')!='base64':raise ValueError('GitHub_inline_content_required')
    return json.loads(base64.b64decode(obj['content']))


def check_control(control,env,current):
    if (env.get('GITHUB_REPOSITORY')!=REPO or env.get('GITHUB_REF')!='refs/heads/feat/decision-evidence-v2'
            or env.get('GITHUB_RUN_ATTEMPT')!='1' or control.get('mode')!='READ_ONLY_NO_PROVIDER_HTTP'
            or control.get('target')!=TARGET or not re.fullmatch('[0-9a-f]{40}',control.get('main_commit',''))
            or not re.fullmatch('[a-z0-9-]{10,100}',control.get('request_id',''))):
        raise ValueError('finite_read_only_control_invalid')
    start=policy.timestamp(control['not_before']);end=policy.timestamp(control['expires_at'])
    if not start<=current<end or not 0<(end-start).total_seconds()<=7200:raise ValueError('finite_read_only_window_closed')
    if any(env.get(k) for k in PROVIDER_ENV):raise ValueError('provider_credential_must_not_enter_audit_process')


def assess(root,snapshot,control,presence):
    root=Path(root);errors=[]
    if snapshot['repository']['default_branch']!='main' or snapshot['main_commit']!=control['main_commit']:errors.append('actual_default_branch_or_main_changed')
    wf=snapshot['workflow'];text=(root/policy.WORKFLOW).read_text()
    if wf.get('state')!='active' or wf.get('path')!=policy.WORKFLOW:errors.append('regular_workflow_inactive_or_wrong_path')
    if text.count('cron:')!=1 or "cron: '30 11 * * 1-5'" not in text:errors.append('regular_cron_mismatch')
    if "if: github.event_name == 'schedule' && steps.freeze.outputs.collect == 'true'" not in text:errors.append('actual_collection_event_gate_changed')
    if any(presence.get(k) is not True for k in ('KIS_APP_KEY','KIS_APP_SECRET')):errors.append('existing_KIS_secret_missing')
    cfg=json.loads((root/'.github/config/kis-regular-access.json').read_bytes());shared=json.loads((root/cfg['shared_policy']).read_bytes())
    proof_raw=(root/shared['native_proof_file']).read_bytes();proof=json.loads(proof_raw)
    if digest(proof_raw)!=shared['native_proof_sha256']:errors.append('native_shared_proof_hash_mismatch')
    hashes=shared['runtime_source_hashes'];native=proof['native'][REPO]['source']['runtime_source_hashes']
    if native!=hashes:errors.append('native_source_map_mismatch')
    changed=[name for name,h in hashes.items() if not (root/name).is_file() or digest((root/name).read_bytes())!=h]
    if changed:errors.append('deployed_runtime_source_changed')
    required=('enabled','both_repositories_deployed','all_account_actors_enrolled','github_access_tested','rate_policy_reviewed','token_policy_reviewed','real_kis_validation_approved_and_passed')
    if cfg.get('mode')!='shared-account-exclusive' or cfg.get('verified') is not True or any(shared.get(k) is not True for k in required):errors.append('shared_rollout_incomplete')
    context=policy.freeze(dict(event='schedule',created_at=TARGET+'T11:30:00Z',head_sha=control['main_commit'],id=1),policy.CRON)
    if not policy.timestamp(shared['reviewed_at'])<=policy.timestamp(context['expires_at'])<policy.timestamp(shared['expires_at']):errors.append('shared_review_not_valid_for_target_window')
    validate_lease(snapshot['lease'])
    owner=snapshot['lease']['owner']
    if owner:errors.append('shared_owner_present_requires_runtime_or_terminal_owner_review')
    if snapshot['ledger'].get('schema')!=1 or snapshot['ledger'].get('year')!='2026':errors.append('regular_year_ledger_invalid')
    slot=snapshot['ledger']['slots'].get(TARGET+'/2030')
    if slot:errors.append('target_slot_already_exists_'+slot['status'])
    return dict(status='BLOCKED_READ_ONLY_CONFIGURATION' if errors else 'PASS_READ_ONLY_CONFIGURATION',errors=errors,
        target=TARGET,scheduled_KST=context['scheduled_at'],scheduled_UTC=policy.timestamp(context['scheduled_at']).astimezone(dt.timezone.utc).isoformat(),
        expires_KST=context['expires_at'],generation_id=context['generation_id'],main_commit=snapshot['main_commit'],
        workflow=policy.WORKFLOW,workflow_state=wf.get('state'),cron=policy.CRON,repository_default_branch=snapshot['repository']['default_branch'],
        secret_presence=presence,reviewed_runtime_files=len(hashes),runtime_hash_failures=changed,
        shared_owner=owner,shared_sequence=snapshot['lease']['sequence'],target_slot_status=slot['status'] if slot else 'UNCLAIMED',
        prior_slots={k:v['status'] for k,v in snapshot['ledger']['slots'].items()},
        active_runs=snapshot.get('active_runs',[]),slot_claimed=False,lease_changed=False,token_issued=False,
        provider_HTTP=0,oauth_HTTP=0,market_HTTP=0,KRX_official_target_day_confirmation='NOT_VERIFIED_BY_THIS_AUDIT',
        actual_authentication='NOT_RUN',actual_collection='NOT_RUN',artifact_publication='NOT_PUBLISHED',
        warnings=['The workflow event must be schedule; workflow_dispatch validates deployment only.',
                  'Provider calendar is KIS, not a KRX official closure notice.',
                  'Current empty ownership does not guarantee no competing owner at the scheduled time.'])


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--operating-tree',required=True);a=p.parse_args()
    c=json.loads(CONTROL.read_bytes());check_control(c,os.environ,dt.datetime.now(dt.timezone.utc))
    root=Path(os.environ['RUNNER_TEMP'])/'regular-preflight-20261102';root.mkdir(exist_ok=False)
    try:
        repository=github_get('repos/'+REPO+'/');main=github_get('repos/'+REPO+'/git/ref/heads/main')['object']['sha']
        refs={b:github_get('repos/'+REPO+'/git/ref/heads/'+b)['object']['sha'] for b in ('kis-shared-account-state','kis-regular-state')}
        snapshot=dict(repository=repository,main_commit=main,workflow=github_get('repos/'+REPO+'/actions/workflows/kis-market-2030.yml'),
            lease=contents('lease.json',refs['kis-shared-account-state']),ledger=contents('ledger/2026.json',refs['kis-regular-state']),active_runs=[])
        for status in policy.ACTIVE:
            runs=github_get('repos/'+REPO+'/actions/runs?status='+status+'&per_page=100')
            if runs['total_count']>100:raise ValueError('active_run_inventory_requires_pagination_review')
            snapshot['active_runs'].extend(dict(run_id=r['id'],path=r['path'],status=r['status']) for r in runs['workflow_runs'])
        presence={k:os.environ.get('PREFLIGHT_'+k+'_PRESENT')=='true' for k in ('KIS_APP_KEY','KIS_APP_SECRET')}
        result=assess(a.operating_tree,snapshot,c,presence);result.update(run_id=os.environ['GITHUB_RUN_ID'],audit_commit=os.environ['GITHUB_SHA'],state_revisions=refs,checked_at=dt.datetime.now(dt.timezone.utc).isoformat())
    except Exception as e:
        result=dict(status='BLOCKED_READ_ONLY_AUDIT',reason=str(e),provider_HTTP=0,oauth_HTTP=0,market_HTTP=0,slot_claimed=False,lease_changed=False)
    (root/'report.json').write_text(json.dumps(result,indent=2)+'\n')
    with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('Read-only November 2 regular preflight; NOT a collection\n\n```json\n'+json.dumps(result,indent=2)+'\n```\n')
    if result['status']!='PASS_READ_ONLY_CONFIGURATION':raise SystemExit(1)


if __name__=='__main__':main()
