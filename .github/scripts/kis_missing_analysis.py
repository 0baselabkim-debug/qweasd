"""Read-only diagnosis of the 113 missing records; never changes collection data."""
import base64, collections, datetime as dt, gzip, hashlib, io, json, os, re
import statistics, sys, time, urllib.error, urllib.parse, urllib.request, zipfile
from pathlib import Path
from zoneinfo import ZoneInfo

DATE = '20261014'
ROOT = Path('missing-analysis')
ROOT.mkdir(exist_ok=True)
SECRETS = [os.environ.get(k, '') for k in ['GH_TOKEN', 'KIS_APP_KEY', 'KIS_APP_SECRET']]
def escape(s): return s.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
for value in SECRETS:
    if value: print('::add-mask::' + escape(value), flush=True)
def stamp(): return dt.datetime.now(ZoneInfo('Asia/Seoul')).isoformat()
def emit(title, obj):
    s = json.dumps(obj, ensure_ascii=False, separators=(',', ':'))
    for value in SECRETS:
        if value: s = s.replace(value, '[REDACTED]')
    print('::notice title=' + title + '::' + escape(s), flush=True)
def save(name, obj):
    (ROOT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')
class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        result = super().redirect_request(req, fp, code, msg, headers, newurl)
        if result and urllib.parse.urlparse(req.full_url).hostname != urllib.parse.urlparse(newurl).hostname:
            result.remove_header('Authorization')
        return result
opener = urllib.request.build_opener(Redirect())
def get(url, headers=None):
    with opener.open(urllib.request.Request(url, headers=headers or {}), timeout=40) as r: return r.read()

def inspect():
    raw = get('https://api.github.com/repos/0baselabkim-debug/qweasd/actions/artifacts/11895319157/zip',
              {'Authorization': 'Bearer ' + SECRETS[0], 'Accept': 'application/vnd.github+json'})
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        state = json.loads(archive.read('state.json'))
    assert state['trade_date'] == DATE and len(state['successes']) == 2430 and len(state['failures']) == 113
    layouts = json.loads(Path(__file__).with_name('kis-master-layouts.json').read_text())
    masters, hashes = {}, []
    for market, layout in layouts.items():
        data = get(layout['url'])
        old = next(x for x in state['master_sources'] if x['market'] == market)
        hashes.append({'market': market, 'downloaded_at': stamp(), 'sha256': hashlib.sha256(data).hexdigest(),
                       'original_sha256': old['sha256'], 'same_as_collection': hashlib.sha256(data).hexdigest() == old['sha256']})
        with zipfile.ZipFile(io.BytesIO(data)) as z: lines = z.read(layout['member']).decode('cp949').splitlines()
        for line in lines:
            width = sum(layout['widths']); prefix, tail = line[:-width], line[-width:]
            code = prefix[:9].strip()
            fields, offset = {}, 0
            for key, size in zip(layout['columns'], layout['widths']):
                fields[key] = tail[offset:offset+size].strip(); offset += size
            masters[code] = {'market': market, 'halt': fields.get('거래정지', fields.get('거래정지 여부')),
                             'listing_date': fields.get('상장일자', fields.get('주식 상장 일자')),
                             'previous_volume_raw': fields.get('전일거래량', fields.get('전일 거래량')),
                             'liquidation': fields.get('정리매매', fields.get('정리매매 여부'))}
    rows = [{'symbol': k, 'name': f['name'], 'market': f['market'], 'original_failure': f,
             'master': masters.get(k)} for k, f in state['failures'].items()]
    durations = [v['request_seconds'] for v in state['successes'].values()]
    failed_durations = [v['seconds'] for v in state['failures'].values()]
    report = {'source_run_id': '38560757360', 'source_artifact_id': 11895319157, 'inspected_at': stamp(),
              'original_successes_sha256': hashlib.sha256(json.dumps(state['successes'], sort_keys=True).encode()).hexdigest(),
              'master_hashes': hashes, 'failure_count': len(rows), 'successful_count': len(state['successes']),
              'halt_flags': dict(collections.Counter((r['master'] or {}).get('halt') for r in rows)),
              'failure_latest_dates': dict(collections.Counter(r['original_failure'].get('latest_received_date') for r in rows)),
              'original_http_statuses': dict(collections.Counter(str(r['original_failure'].get('http_status')) for r in rows)),
              'listing_on_target_date': [r['symbol'] for r in rows if (r['master'] or {}).get('listing_date') == DATE],
              'original_timing': {'start': state['first_collection_started_at'], 'end': state['run_collection_completed_at'],
                  'total_requests': sum(state['attempts'].values()), 'success_request_seconds_mean': statistics.mean(durations),
                  'success_request_seconds_sum': sum(durations), 'success_request_seconds_p95': sorted(durations)[int(len(durations)*.95)-1],
                  'last_failure_request_seconds_mean': statistics.mean(failed_durations)},
              'rows': rows}
    save('inspection.json', report)
    emit('Missing inspection', {k: v for k, v in report.items() if k != 'rows'})

LAST = 0.0
def api(path, headers, body=None):
    global LAST
    time.sleep(max(0, 1.05 - (time.monotonic() - LAST)))
    LAST = time.monotonic(); started = stamp()
    try:
        req = urllib.request.Request('https://openapi.koreainvestment.com:9443' + path, headers=headers, data=body)
        with urllib.request.urlopen(req, timeout=20) as r: status, raw = r.status, r.read()
    except urllib.error.HTTPError as e: status, raw = e.code, e.read()
    except Exception as e: return {}, {'http_status': None, 'error_type': type(e).__name__, 'started_at': started, 'finished_at': stamp()}
    try: payload = json.loads(raw)
    except (ValueError, UnicodeError): payload = {}
    if not isinstance(payload, dict): payload = {}
    meta = {'http_status': status, 'api_code': str(payload.get('msg_cd', payload.get('error_code', '')))[:40],
            'rt_cd': payload.get('rt_cd'), 'started_at': started, 'finished_at': stamp(), 'seconds': round(time.monotonic()-LAST, 3)}
    if status == 429 or meta['api_code'] == 'EGW00201': time.sleep(5)
    return payload, meta
def number(value):
    return int(str(value)) if re.fullmatch(r'-?\d+', str(value)) else None
def probe():
    report = json.loads((ROOT / 'inspection.json').read_text())
    payload, meta = api('/oauth2/tokenP', {'Content-Type': 'application/json'},
        json.dumps({'grant_type': 'client_credentials', 'appkey': SECRETS[1], 'appsecret': SECRETS[2]}).encode())
    token = payload.get('access_token')
    if meta['http_status'] != 200 or not token:
        emit('Diagnosis authentication failed', meta); return 1
    SECRETS.append(token); print('::add-mask::'+escape(token), flush=True)
    headers = {'authorization': 'Bearer '+token, 'appkey': SECRETS[1], 'appsecret': SECRETS[2], 'custtype': 'P'}
    halted = [r['symbol'] for r in report['rows'] if (r['master'] or {}).get('halt') == 'Y']
    sample = set(halted[:3] + halted[-2:])
    request_count = 0
    for idx, row in enumerate(report['rows']):
        code = row['symbol']
        params = {'FID_COND_MRKT_DIV_CODE':'J', 'FID_INPUT_ISCD':code, 'FID_INPUT_DATE_1':DATE,
                  'FID_INPUT_DATE_2':DATE, 'FID_PERIOD_DIV_CODE':'D', 'FID_ORG_ADJ_PRC':'1'}
        data, meta = api('/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice?'+urllib.parse.urlencode(params), headers|{'tr_id':'FHKST03010100'})
        request_count += 1
        entries = data.get('output2', []); entries = entries if isinstance(entries, list) else []
        matches = [r for r in entries if isinstance(r, dict) and r.get('stck_bsop_date') == DATE]
        row['price'] = {'request': meta, 'target_date_row_count':len(matches),
            'target_rows': [{k:r.get(k) for k in ['stck_bsop_date','acml_vol','acml_tr_pbmn','stck_clpr','stck_oprc']} for r in matches],
            'returned_dates': [r.get('stck_bsop_date') for r in entries if isinstance(r,dict) and r.get('stck_bsop_date')]}
        should_flow = code in sample or (row['master'] or {}).get('halt') != 'Y'
        if len(matches) == 1 and number(matches[0].get('acml_vol')) not in (0, None): should_flow = True
        if should_flow:
            params = {'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':code,'FID_INPUT_DATE_1':DATE,'FID_ORG_ADJ_PRC':'','FID_ETC_CLS_CODE':''}
            data, meta = api('/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily?'+urllib.parse.urlencode(params),headers|{'tr_id':'FHPTJ04160001'})
            request_count += 1
            entries = data.get('output2', []); entries = entries if isinstance(entries,list) else []
            row['flow_recheck'] = {'request':meta,'target_date_row_count':sum(r.get('stck_bsop_date')==DATE for r in entries if isinstance(r,dict)),
                'latest_rows':[{k:r.get(k) for k in ['stck_bsop_date','acml_vol','frgn_ntby_qty','frgn_ntby_tr_pbmn']} for r in entries[:2] if isinstance(r,dict)]}
        save('diagnosis.json',report|{'diagnosis_finished_at':stamp(),'additional_market_requests':request_count,'processed_symbols':idx+1})
        if (idx+1)%25==0: print(json.dumps({'processed':idx+1,'requests':request_count}),flush=True)
    return 0
def export():
    name = 'diagnosis.json' if (ROOT/'diagnosis.json').exists() else 'inspection.json'
    report = json.loads((ROOT/name).read_text())
    packed = base64.b64encode(gzip.compress(json.dumps(report,ensure_ascii=False,separators=(',',':')).encode())).decode()
    chunks = [packed[i:i+2800] for i in range(0,len(packed),2800)]
    if len(chunks)>9: raise RuntimeError('report_exceeds_annotation_budget')
    for i,chunk in enumerate(chunks): emit('Missing diagnosis data',{'part':i,'parts':len(chunks),'gzip_b64':chunk})
if __name__ == '__main__':
    try:
        if sys.argv[1]=='inspect': inspect()
        elif sys.argv[1]=='probe': sys.exit(probe())
        elif sys.argv[1]=='export': export()
    except Exception as e:
        emit('Diagnosis stopped', {'error_type':type(e).__name__});sys.exit(1)
