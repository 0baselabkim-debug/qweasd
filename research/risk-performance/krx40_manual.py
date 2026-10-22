"""Manual research orchestration. No scheduler, KIS transport or signal writer."""
import json
import os
from pathlib import Path

import krx40_study as study
from krx40_collect import (BRANCH, OUT, REPO, Journal, git, utcnow,
                           require_window, persist_research)
from krx_validation import canonical, write_once


def atomic_json(path, data):
    tmp = path.with_suffix('.tmp')
    tmp.write_bytes(canonical(data))
    tmp.replace(path)


def execute(mode, retry_failed=False):
    if mode not in ('prepare', 'collect-batch'):
        raise ValueError('unsupported_manual_mode')
    if git('branch', '--show-current') != BRANCH:
        raise ValueError('research_branch_required')
    run = os.environ.get('GITHUB_RUN_ID', '')
    attempt = os.environ.get('GITHUB_RUN_ATTEMPT', '')
    if not run.isdigit() or not attempt.isdigit():
        raise ValueError('manual_Actions_identity_required')
    directory = OUT/'manual-executions'/(run+'-'+attempt)
    start = {'mode': mode, 'retry_failed': retry_failed, 'started_at': utcnow().isoformat(),
             'code_commit': git('rev-parse', 'HEAD'), 'run_id': run, 'run_attempt': attempt,
             'run_url': 'https://github.com/0baselabkim-debug/qweasd/actions/runs/'+run}
    write_once(directory/'START.json', canonical(start))
    phase = 'time_gate'; code = 1; summary = None; batch = None
    result = {**start, 'registered_signals': 0}
    try:
        if mode == 'collect-batch':
            require_window(utcnow())
        phase = 'fresh_publication_checkpoint_calendar_plan'
        execution, summary = study.prepare_latest()
        result['preparation'] = execution
        if mode == 'collect-batch':
            phase = 'durable_preparation'
            persist_research()
            phase = 'bounded_collection'
            batch = study.execute_batch(REPO/execution['plan'], retry_failed)
            result['batch_requests'] = batch['batch_requests']
            phase = 'post_collection_reclassification'
            execution, summary = study.prepare(summary['source_commit'])
            result['post_collection'] = execution
            newly_attempted = batch['requests'][-batch['batch_requests']:] if batch['batch_requests'] else []
            if any(r.get('status') != 'validated' for r in newly_attempted):
                result['status'] = 'batch_failed_evidence_preserved'
            elif summary['minimum_missing_requests'] and not batch['batch_requests']:
                result['status'] = 'no_progress_pending_evidence_review'
            else:
                result['status'] = ('batch_completed_remaining' if summary['minimum_missing_requests']
                                    else 'collection_complete_validation_separate')
                code = 0
        else:
            result['status'] = 'offline_preparation_complete_no_market_requests'
            code = 0
    except Exception as error:
        # Network exception text can contain tokens/URLs: never serialize it.
        result.update(status='blocked_or_failed', failed_phase=phase, error_type=type(error).__name__)
        safe = {'outside_approved_2300_0900_KST_window', 'dispatch_not_in_current_approved_night',
                'other_operation_active_or_waiting', 'main_changed_refresh_read_only_plan_before_collection',
                'research_remote_has_changed_no_concurrent_publication', 'remaining_plan_exceeds_remaining_authorization',
                'existing_KRX_credential_not_available', 'uncertain_prior_request_requires_evidence_review',
                'prior_access_or_rate_limit_requires_review_no_automatic_retry',
                'published_checkpoint_identity_or_completion_mismatch', 'published_checkpoint_record_identity_mismatch',
                'captured_checkpoint_hash_mismatch', 'stale_plan_refresh_required'}
        if isinstance(error, ValueError) and str(error) in safe:
            result['reason'] = str(error)
    if summary is not None:
        keys = ('asof', 'population', 'official_40_complete', 'price_validation_pass', 'known_adjustment_holds',
                'known_alphachips_hold', 'original_20day_flow_pass', 'original_flow_hold', 'groups',
                'unclassifiable', 'condition_not_met', 'minimum_missing_requests', 'source_commit')
        result['coverage'] = {key: summary[key] for key in keys if key in summary}
    # A collection exception may leave an uncertain reservation. Never reset it.
    try:
        result['api_execution'] = Journal(OUT/'collection').summary()
    except Exception as error:
        result.update(status='blocked_corrupt_request_journal', journal_error_type=type(error).__name__)
        code = 1
    result['finished_at'] = utcnow().isoformat()
    write_once(directory/'RESULT.json', canonical(result))
    atomic_json(OUT/'LATEST_MANUAL_RESULT.json', result)
    if code == 0 and summary is not None:
        atomic_json(OUT/'CURRENT_RUN.json', {
            **execution, 'asof': summary['asof'], 'plan_sha256': summary['plan_sha256'],
            'collection_status': result['status'],
            'market_API_calls': result['api_execution']['confirmed_physical_requests']})
    print(json.dumps({key: result.get(key) for key in ('status', 'reason', 'coverage', 'run_url')}, ensure_ascii=False))
    summary_file = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary_file:
        with open(summary_file, 'a') as stream:
            stream.write('```json\n'+json.dumps({k: result.get(k) for k in ('status', 'reason', 'coverage')},
                                               ensure_ascii=False, indent=2)+'\n```\n')
            api = result.get('api_execution', {})
            stream.write(f"Physical requests: {api.get('confirmed_physical_requests', 'unknown')}; "
                         f"charged: {api.get('charged_requests', 'unknown')}/80; signals registered: 0.\n")
    return code


if __name__ == '__main__':
    raise SystemExit(execute(os.environ.get('RESEARCH_MODE', 'prepare'),
                            os.environ.get('RETRY_FAILED', 'false') == 'true'))
