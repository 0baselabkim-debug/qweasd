"""2026-10-14 KRX foreign-flow collection, authorized before finality verification.
No account/order endpoints. No application DB writes. No live calls before 20:30 KST.
"""
import collections
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from zoneinfo import ZoneInfo

ZONE = ZoneInfo('Asia/Seoul')
TRADE_DATE = '20261014'
PLANNED = dt.datetime(2026, 10, 14, 20, 30, tzinfo=ZONE)
OUT = Path('kis-results')
ARTIFACT = 'kis-daily-20261014'
BASE = 'https://openapi.koreainvestment.com:9443'
RUN_ID = os.environ.get('GITHUB_RUN_ID', '')
REPO = os.environ.get('GITHUB_REPOSITORY', '')
SECRETS = [os.environ.get(k, '') for k in ['KIS_APP_KEY', 'KIS_APP_SECRET', 'GITHUB_TOKEN']]
KEY, SECRET, GH_TOKEN = SECRETS
STATE = None
LAST_CALL = 0.0
BOOT = dt.datetime.now(ZONE)
DEADLINE = time.monotonic() + 230 * 60

def now():
    return dt.datetime.now(ZONE).isoformat()

def escape(s):
    return s.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')

def log(event, **data):
    raw = json.dumps({'time': now(), 'event': event, **data}, ensure_ascii=False)
    for value in SECRETS:
        if value:
            raw = raw.replace(value, '[REDACTED]')
    print(raw, flush=True)

for value in SECRETS:
    if value:
        print('::add-mask::' + escape(value), flush=True)

class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        result = super().redirect_request(req, fp, code, msg, headers, newurl)
        if result and urllib.parse.urlparse(req.full_url).hostname != urllib.parse.urlparse(newurl).hostname:
            result.remove_header('Authorization')
        return result

OPENER = urllib.request.build_opener(SafeRedirect())

def get_bytes(url, headers=None, max_bytes=25_000_000):
    with OPENER.open(urllib.request.Request(url, headers=headers or {}), timeout=30) as r:
        raw = r.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError('response_too_large')
        return raw

def gh(path):
    return json.loads(get_bytes('https://api.github.com/repos/' + REPO + path,
                               {'Authorization': 'Bearer ' + GH_TOKEN, 'Accept': 'application/vnd.github+json'}))

def atomic_json(path, data):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)

def restore():
    # A prior artifact is the resume ledger. Never restart successful symbols blindly.
    listing = gh('/actions/artifacts?' + urllib.parse.urlencode({'name': ARTIFACT, 'per_page': 100}))
    artifacts = sorted([a for a in listing.get('artifacts', []) if a.get('name') == ARTIFACT and not a.get('expired')],
                       key=lambda a: a['created_at'], reverse=True)
    if not artifacts:
        return None
    # Error-only artifacts can lack state.json; preserve an earlier valid ledger.
    for artifact in artifacts:
        raw = get_bytes('https://api.github.com/repos/' + REPO + '/actions/artifacts/' + str(artifact['id']) + '/zip',
                        {'Authorization': 'Bearer ' + GH_TOKEN, 'Accept': 'application/vnd.github+json'})
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = [n for n in archive.namelist() if n == 'state.json' or n.endswith('/state.json')]
            if not members:
                continue
            if len(members) != 1 or archive.getinfo(members[0]).file_size > 20_000_000:
                raise ValueError('invalid_resume_artifact')
            state = json.loads(archive.read(members[0]))
        if state.get('schema_version') != 1 or state.get('trade_date') != TRADE_DATE or state.get('exchange') != 'KRX':
            raise ValueError('resume_scope_mismatch')
        state['resumed_from_run_id'] = str(artifact.get('workflow_run', {}).get('id', ''))
        log('resume_loaded', previous_run_id=state['resumed_from_run_id'], successful_symbols=len(state.get('successes', {})))
        return state
    # No earlier collection state existed (e.g. all attempts failed before master loading).
    return None

def universe():
    layouts = json.loads(Path(__file__).with_name('kis-master-layouts.json').read_text())
    selected, excluded, sources = [], [], []
    for market, layout in layouts.items():
        raw = get_bytes(layout['url'])  # default certificate verification stays enabled
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entry = archive.getinfo(layout['member'])
            if entry.file_size > 20_000_000:
                raise ValueError('oversized_master')
            text = archive.read(layout['member']).decode('cp949')
        width = sum(layout['widths'])
        count = 0
        for line in text.splitlines():
            if not line.strip():
                continue
            count += 1
            if len(line) < width + 21:
                raise ValueError('master_layout_changed')
            prefix, tail = line[:-width], line[-width:]
            code, isin, name = prefix[:9].strip(), prefix[9:21].strip(), prefix[21:].strip()
            fields, offset = [], 0
            for size in layout['widths']:
                fields.append(tail[offset:offset + size].strip())
                offset += size
            group = fields[layout['group_index']]
            preferred = fields[layout['preferred_index']]
            listing_date = fields[layout['listing_date_index']]
            reason = None
            if not re.fullmatch(r'\d{6}', code):
                reason = 'not_six_digit_equity'
            elif group != 'ST':
                reason = 'not_stock_group_ST'
            elif preferred != '0':
                reason = 'preferred_or_unknown_share_class'
            elif listing_date and (not re.fullmatch(r'\d{8}', listing_date) or listing_date > TRADE_DATE):
                reason = 'listing_date_outside_scope'
            row = {'symbol': code, 'name': name, 'market': market, 'isin': isin,
                   'group_code': group, 'preferred_code': preferred, 'listing_date': listing_date}
            if reason:
                excluded.append(row | {'reason': reason})
            else:
                selected.append(row)
        sources.append({'market': market, 'url': layout['url'], 'sha256': hashlib.sha256(raw).hexdigest(),
                        'downloaded_at': now(), 'master_rows': count, 'layout_source': layout['source']})
    if len(selected) < 100 or len(selected) > 10000 or len({r['symbol'] for r in selected}) != len(selected):
        raise ValueError('universe_validation_failed')
    selected.sort(key=lambda r: (r['market'], r['symbol']))
    return selected, excluded, sources

def api(path, headers, body=None):
    global LAST_CALL
    time.sleep(max(0, 1.05 - (time.monotonic() - LAST_CALL)))
    LAST_CALL = time.monotonic()
    started = time.monotonic()
    try:
        req = urllib.request.Request(BASE + path, headers=headers, data=body)
        with urllib.request.urlopen(req, timeout=20) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    except Exception as error:
        return {}, {'http_status': None, 'error_type': type(error).__name__, 'seconds': round(time.monotonic() - started, 3)}
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return payload, {'http_status': status, 'api_code': str(payload.get('msg_cd', payload.get('error_code', '')))[:40],
                     'seconds': round(time.monotonic() - started, 3)}

def authenticate():
    payload, meta = api('/oauth2/tokenP', {'Content-Type': 'application/json'},
                        json.dumps({'grant_type': 'client_credentials', 'appkey': KEY, 'appsecret': SECRET}).encode())
    token = payload.get('access_token')
    if meta['http_status'] != 200 or not isinstance(token, str) or not token:
        log('authentication_failed', **meta)
        raise RuntimeError('authentication_failed')
    SECRETS.append(token)
    print('::add-mask::' + escape(token), flush=True)
    log('authentication_succeeded', http_status=200)
    return {'authorization': 'Bearer ' + token, 'appkey': KEY, 'appsecret': SECRET,
            'tr_id': 'FHPTJ04160001', 'custtype': 'P', 'Content-Type': 'application/json'}

def collect(symbol, headers):
    params = {'FID_COND_MRKT_DIV_CODE': 'J', 'FID_INPUT_ISCD': symbol, 'FID_INPUT_DATE_1': TRADE_DATE,
              'FID_ORG_ADJ_PRC': '', 'FID_ETC_CLS_CODE': ''}
    started_at = now()
    payload, meta = api('/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily?' + urllib.parse.urlencode(params), headers)
    meta.update({'started_at': started_at, 'completed_at': now()})
    if meta['http_status'] != 200 or payload.get('rt_cd') != '0':
        return None, meta | {'reason': 'request_failed'}
    rows = payload.get('output2', [])
    if not isinstance(rows, list):
        return None, meta | {'reason': 'unexpected_schema'}
    matches = [r for r in rows if isinstance(r, dict) and r.get('stck_bsop_date') == TRADE_DATE]
    if len(matches) != 1:
        dates = sorted({str(r.get('stck_bsop_date', '')) for r in rows if isinstance(r, dict) and re.fullmatch(r'\d{8}', str(r.get('stck_bsop_date', '')))})
        return None, meta | {'reason': 'target_date_missing_or_duplicate', 'latest_received_date': dates[-1] if dates else None}
    row = matches[0]
    required = ['frgn_ntby_qty', 'frgn_ntby_tr_pbmn', 'frgn_shnu_vol', 'frgn_seln_vol', 'frgn_shnu_tr_pbmn', 'frgn_seln_tr_pbmn']
    if any(not re.fullmatch(r'-?\d+', str(row.get(k, ''))) for k in required):
        return None, meta | {'reason': 'missing_or_invalid_foreign_flow'}
    numeric = {k: int(row[k]) for k in required}
    if numeric['frgn_shnu_vol'] - numeric['frgn_seln_vol'] != numeric['frgn_ntby_qty']:
        return None, meta | {'reason': 'quantity_identity_mismatch'}
    difference = numeric['frgn_shnu_tr_pbmn'] - numeric['frgn_seln_tr_pbmn'] - numeric['frgn_ntby_tr_pbmn']
    if abs(difference) > 1:
        return None, meta | {'reason': 'amount_difference_exceeds_one_source_unit', 'difference_million_krw': difference}
    safe = {k: str(row[k]) for k in required}
    for k in ['stck_clpr', 'acml_vol', 'acml_tr_pbmn']:
        if re.fullmatch(r'\d+', str(row.get(k, ''))):
            safe[k] = str(row[k])
    result = {'symbol': symbol, 'trade_date': TRADE_DATE, 'exchange': 'KRX', 'source_endpoint': 'FHPTJ04160001',
              'source_amount_unit': 'million_KRW', 'foreign_net_amount_krw': numeric['frgn_ntby_tr_pbmn'] * 1_000_000,
              'foreign_net_quantity_shares': numeric['frgn_ntby_qty'], 'source_fields': safe,
              'amount_identity_difference_million_krw': difference,
              'warning': 'amount_quantization_difference' if difference else None,
              'finality': 'UNVERIFIED', 'received_at': meta['completed_at'], 'request_seconds': meta['seconds']}
    return result, meta

def save(status, reason=None):
    STATE.update({'status': status, 'updated_at': now(), 'last_run_id': RUN_ID})
    if reason:
        STATE['last_reason'] = reason
    atomic_json(OUT / 'state.json', STATE)
    total = len(STATE['universe'])
    done = len(STATE['successes'])
    failed = len(STATE['failures'])
    report = {k: STATE.get(k) for k in ['trade_date', 'exchange', 'planned_start_at', 'first_collection_started_at',
              'actual_run_started_at', 'run_collection_started_at', 'run_collection_completed_at', 'completed_at',
              'status', 'updated_at', 'last_run_id', 'resumed_from_run_id', 'last_reason']}
    report.update({'target_symbols': total, 'successful_symbols': done, 'failed_symbols': failed,
                   'pending_symbols': total - done - failed, 'coverage_percent': round(done / total * 100, 2) if total else 0,
                   'finality': 'UNVERIFIED', 'finality_label': '원천 최종확정 미확인',
                   'schedule_lag_seconds': max(0, (BOOT - PLANNED).total_seconds()),
                   'source_master_counts': STATE.get('master_sources', []),
                   'ordinary_stock_filter': 'KIS stock group ST, preferred code 0, six-digit code, listed by target date',
                   'failures': STATE['failures'], 'run_url': 'https://github.com/' + REPO + '/actions/runs/' + RUN_ID})
    atomic_json(OUT / 'status.json', report)
    atomic_json(OUT / 'failed-symbols.json', STATE['failures'])
    names = {r['symbol']: r for r in STATE['universe']}
    with (OUT / 'foreign-flow.csv').open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=['trade_date', 'market', 'symbol', 'name', 'exchange', 'foreign_net_amount_krw',
                                                 'foreign_net_quantity_shares', 'amount_identity_difference_million_krw', 'finality', 'received_at'])
        writer.writeheader()
        for code, result in sorted(STATE['successes'].items()):
            writer.writerow({k: (names[code].get(k) if k in ['market', 'name'] else result.get(k)) for k in writer.fieldnames})
    lines = ['# 오늘 KIS 전체 보통주 수급 수집', '', '**원천 최종확정 미확인 — 수집 결과이며 최종 스크리닝 결과가 아닙니다.**', '',
             f"- 기준일: {TRADE_DATE} / KRX", f"- 상태: {status}", f"- 성공: {done}/{total}, 실패: {failed}, 대기: {total-done-failed}",
             f"- 예약 시각: {PLANNED.isoformat()}", f"- 이번 실행 시작: {BOOT.isoformat()}",
             f"- 수집 시작: {STATE.get('run_collection_started_at')}", f"- 수집 완료/중단: {STATE.get('run_collection_completed_at')}",
             f"- 최근 갱신: {report['updated_at']}", '', '완료 파일은 이 실행의 Artifacts에서 kis-daily-20261014를 내려받으세요.',
             'status.json: 진행 현황 / foreign-flow.csv: 수급 / failed-symbols.json: 실패 목록 / state.json: 재개 체크포인트']
    Path(os.environ['GITHUB_STEP_SUMMARY']).write_text('\n'.join(lines) + '\n')
    log('progress', status=status, successful=done, failed=failed, pending=total-done-failed, total=total)

def main():
    global STATE
    if BOOT < PLANNED:
        log('not_due', planned_start_at=PLANNED.isoformat());return 0
    if BOOT > PLANNED + dt.timedelta(days=7):
        log('expired_one_date_schedule', target_trade_date=TRADE_DATE);return 0
    if not KEY or not SECRET or not GH_TOKEN:
        log('missing_credentials', missing_names=[k for k in ['KIS_APP_KEY', 'KIS_APP_SECRET', 'GITHUB_TOKEN'] if not os.environ.get(k)]);return 1
    OUT.mkdir(exist_ok=True)
    STATE = restore()
    if STATE is None:
        STATE = {'schema_version': 1, 'trade_date': TRADE_DATE, 'exchange': 'KRX', 'planned_start_at': PLANNED.isoformat(),
                 'universe': [], 'successes': {}, 'failures': {}, 'attempts': {}, 'finality': 'UNVERIFIED', 'created_at': now()}
    STATE['actual_run_started_at'] = BOOT.isoformat()
    if STATE.get('status') == 'COMPLETED':
        save('COMPLETED', 'already_completed_no_market_requests');return 0
    if not STATE['universe']:
        save('PREPARING')
        targets, exclusions, sources = universe()
        STATE.update({'universe': targets, 'excluded_instruments': exclusions, 'master_sources': sources})
        atomic_json(OUT / 'universe.json', targets)
        atomic_json(OUT / 'excluded-instruments.json', exclusions)
    STATE['run_collection_started_at'] = now()
    STATE.setdefault('first_collection_started_at', STATE['run_collection_started_at'])
    STATE['run_collection_completed_at'] = None
    save('RUNNING')
    headers = authenticate()
    for round_number in range(3):
        pending = [r for r in STATE['universe'] if r['symbol'] not in STATE['successes']]
        if not pending:
            break
        if round_number:
            log('retry_failed_only', round=round_number + 1, count=len(pending));time.sleep(30)
        for i, row in enumerate(pending):
            if time.monotonic() >= DEADLINE:
                STATE['run_collection_completed_at'] = now();save('PARTIAL', 'time_budget_reached');return 1
            code = row['symbol']
            result, meta = collect(code, headers)
            STATE['attempts'][code] = STATE['attempts'].get(code, 0) + 1
            if result is not None:
                STATE['successes'][code] = result;STATE['failures'].pop(code, None)
            else:
                STATE['failures'][code] = {'symbol': code, 'name': row['name'], 'market': row['market'], 'attempts': STATE['attempts'][code], **meta}
                log('symbol_failed', symbol=code, reason=meta.get('reason'), http_status=meta.get('http_status'), api_code=meta.get('api_code'))
                if meta.get('http_status') in [401, 403] or meta.get('api_code') in ['EGW00121', 'EGW00123']:
                    STATE['run_collection_completed_at'] = now();save('PARTIAL', 'authentication_or_access_rejected');return 1
                if meta.get('http_status') == 429 or meta.get('api_code') == 'EGW00201':
                    time.sleep(5)
            if (i + 1) % 25 == 0:
                save('RUNNING' if round_number == 0 else 'RETRYING')
        save('RUNNING' if round_number == 0 else 'RETRYING')
    STATE['run_collection_completed_at'] = now()
    complete = len(STATE['successes']) == len(STATE['universe']) and bool(STATE['universe'])
    if complete:
        STATE['completed_at'] = now()
    save('COMPLETED' if complete else 'PARTIAL')
    return 0 if complete else 1

try:
    sys.exit(main())
except Exception as error:
    log('collection_stopped', error_type=type(error).__name__)
    if STATE is not None:
        STATE['run_collection_completed_at'] = now()
        save('PARTIAL' if STATE.get('universe') else 'PREPARATION_FAILED', type(error).__name__)
    else:
        OUT.mkdir(exist_ok=True)
        atomic_json(OUT / 'status.json', {'status': 'RESUME_OR_PREPARATION_FAILED', 'error_type': type(error).__name__,
                    'planned_start_at': PLANNED.isoformat(), 'actual_run_started_at': BOOT.isoformat(),
                    'actual_completed_at': now(), 'trade_date': TRADE_DATE, 'finality': 'UNVERIFIED'})
    sys.exit(1)
