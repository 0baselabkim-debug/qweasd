"""Measured one-generation diagnostics; never changes collection policy or values."""
from collections import Counter
from .providers import Q


def analyze(report,ledger):
    seconds=report['metrics']['seconds'];elapsed=report['elapsed_seconds']
    starts=[e for e in ledger if e['event']=='started']
    endpoints=Counter(e['endpoint'] for e in starts)
    timing={
        'API_response':report['pure_api_seconds'],
        'rate_wait':seconds.get('rate_wait',0),
        'backoff':sum(seconds.get(k,0) for k in ('backoff_wait','reauth_backoff','provider_circuit_wait')),
        'raw_hash':seconds.get('source_hash',0),
        'raw_storage_compression':seconds.get('raw_compression_and_storage',0),
        'parsing':seconds.get('parse',0),
        'internal_validation':seconds.get('internal_validation',0),
        'DuckDB':seconds.get('DuckDB_write_and_readback',0),
        'JSON':seconds.get('JSON_generation_and_storage',0),
        'preserved_response_rescan':seconds.get('preserved_response_rescan',0),
        'raw_DB_reverification':seconds.get('raw_DB_reverification',0),
        'progress_reporting':seconds.get('progress_reporting',0),
    }
    shares={k:v/elapsed*100 if elapsed else None for k,v in timing.items()}
    storage_validation=sum(timing[k] for k in ('raw_hash','raw_storage_compression','parsing','internal_validation','DuckDB','JSON','preserved_response_rescan','raw_DB_reverification'))
    floor=(report['target']*2+2)*1.05
    reasons=Counter(reason for r in report.get('excluded_symbols',[]) for reason in r['reasons'])
    complete=report['status']=='PASS' and report['kis_validated']==report['target']
    duplicate_work=report.get('physical_retries',0)
    retry_explained=report.get('task_failures',0)>0 or any(e.get('reason')=='authentication' and e.get('repeated_signature') for e in starts)
    return dict(status='COMPLETE_PROVISIONAL' if complete else 'INCOMPLETE',asof=report['asof'],provider_finality='UNVERIFIED',official_price_status='PENDING_KRX',
        target=report['target'],received=report['received_symbols'],validated=report['kis_validated'],failed_or_missing=report['target']-report['kis_validated'],
        first_attempt_success_rate=report['first_attempt_success_rate'],first_attempt_success_denominator=report['first_attempt_success_denominator'],final_validation_rate=report['final_validation_rate'],
        elapsed_seconds=elapsed,preparation_seconds=report.get('preparation_seconds'),end_to_end_seconds=report.get('end_to_end_seconds'),phase_seconds=timing,phase_percent=shares,
        storage_validation_seconds=storage_validation,unattributed_seconds=max(0,elapsed-sum(timing.values())),
        physical_http=report['physical_http'],failed_http=report['failed_http_requests'],physical_retries=duplicate_work,recovered_tasks=report['recovered_tasks'],recovery_measured_seconds=report['retry_extra_seconds'],
        recovery_time_semantics=report['retry_time_semantics'],endpoint_counts=dict(endpoints),symbols_per_minute=report['symbols_per_minute'],
        primary_bottleneck=max(timing,key=timing.get),
        authentication_requests=endpoints['/oauth2/tokenP'],index_requests=endpoints[Q+'inquire-daily-indexchartprice'],
        expected_baseline_http=report['target']*2+3,unnecessary_repeat_evidence='none; every signature is unique' if not duplicate_work else 'Repeated signatures present; inspect failures and reauthentication, not automatically unnecessary',
        repeated_requests_have_failure_or_auth_context=retry_explained if duplicate_work else None,
        master_population=3950,excluded_count=len(report.get('excluded_symbols',[])),exclusion_reason_counts_nonexclusive=dict(reasons),duplicate_master_codes=report['duplicate_master_codes'],duplicate_source_failure_count=len(report['duplicate_source_failures']),
        safe_configuration_options=[
            dict(change='Keep one authentication and two shared index requests per generation; reuse only same-generation successes',measured_auth_requests=endpoints['/oauth2/tokenP'],measured_index_requests=endpoints[Q+'inquire-daily-indexchartprice'],additional_gain_claimed=False),
            dict(change='Adjust progress-report frequency while keeping durable per-request journals',measured_seconds=timing['progress_reporting'],hypothetical_maximum_elapsed_reduction_percent=shares['progress_reporting'],applied=False)],
        code_optimizations_requiring_tests=[
            dict(change='Persistent HTTPS connection reuse with identical TLS, redirect and budget rules',request_spacing_floor_seconds=floor,hypothetical_gap_to_spacing_floor_seconds=max(0,elapsed-floor) if complete else None,effect_verified=False),
            dict(change='Explicit DuckDB transaction around batch inserts',measured_seconds=timing['DuckDB'],hypothetical_maximum_elapsed_reduction_percent=shares['DuckDB'],effect_verified=False),
            dict(change='Cache parsed values bound to source hashes and task identity; retain all validation and raw integrity checks',measured_parse_and_validation_seconds=timing['parsing']+timing['internal_validation']+timing['preserved_response_rescan']+timing['raw_DB_reverification'],effect_verified=False),
            dict(change='Benchmark lossless gzip levels on preserved actual raw bytes before selecting a later-generation setting',measured_seconds=timing['raw_storage_compression'],hypothetical_maximum_elapsed_reduction_percent=shares['raw_storage_compression'],effect_verified=False)],
        prohibited_shortcuts=['No request interval below 1.05 seconds without a newly authorized policy','No dropping either per-symbol endpoint or weakening validation','No copying market observations between generations','No automatic schedule or new generation'],
        next_experiment_requirements=['Explicit new generation ID and approved observation window','Separate immutable generation directories and per-generation budgets bound to a shared history','Fresh flow and price HTTP for all eligible symbols plus indices; cache reuse only inside the same generation','Reverify the same master/population and preserve per-symbol actual response times','Compare normalized values by symbol, market and asof; distinguish null and zero; compare hashes/times separately','Require complete populations for complete equality; keep independent KRX attestations pending until supplied'],
        optimization_gains_are_upper_bounds_not_validated_speedups=True)


def markdown_table(report,analysis):
    values=[('수집 대상 종목',report['target']),('실제 수집 종목',report['received_symbols']),('검증 통과 종목',report['kis_validated']),('미수집·검증 실패',report['target']-report['kis_validated']),('전체 소요시간',f"{report['elapsed_seconds']:.3f}초"),('실제 HTTP 요청',report['physical_http']),('실패 및 재시도',f"HTTP 실패 {report['failed_http_requests']} / 물리 재시도 {report['physical_retries']} / 복구 작업 {report['recovered_tasks']}"),('API 대기시간',f"간격 {analysis['phase_seconds']['rate_wait']:.3f}초 / 백오프 {analysis['phase_seconds']['backoff']:.3f}초"),('저장·검증시간',f"{analysis['storage_validation_seconds']:.3f}초"),('주요 병목',analysis['primary_bottleneck']),('다음 개선 방향','측정 비중을 기준으로 연결 재사용·DB 트랜잭션·중복 파싱을 검증; 검증된 개선 효과 없음')]
    return '| 항목 | 실측 결과 |\n|---|---|\n'+'\n'.join('| '+str(k)+' | '+str(v)+' |' for k,v in values)+'\n'
