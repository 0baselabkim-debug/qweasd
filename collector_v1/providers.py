"""Only repository-evidenced official endpoints and parameter combinations."""
import datetime as dt
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
from .core import Failure, now, sha
from zoneinfo import ZoneInfo

BASE = 'https://openapi.koreainvestment.com:9443'
Q = '/uapi/domestic-stock/v1/quotations/'
LAYOUTS = json.loads((Path(__file__).resolve().parents[1]/'.github/scripts/kis-master-layouts.json').read_text())
CREDENTIALS = {'KIS': ('KIS_APP_KEY','KIS_APP_SECRET'), 'KRX': ('KRX_API_KEY',), 'DART': ('DART_API_KEY',), 'ECOS': ('ECOS_API_KEY',)}


def task(kind, asof, **kwargs):
    dt.date.fromisoformat(asof)
    return dict(kind=kind, asof=asof, **kwargs)


def market_tasks(asof, universe):
    out = [task('calendar', asof)]
    for market in ('KOSPI', 'KOSDAQ'):
        out += [task('krx', asof, market=market), task('index', asof, market=market)]
    for row in universe:
        if row['eligible']:
            out += [task(k, asof, symbol=row['symbol'], market=row['market']) for k in ('flow', 'price')]
    return out


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Credentials may never follow a redirect to another destination.
        return None


class Providers:
    def __init__(self, store, budget, opener=None):
        self.store, self.budget = store, budget
        self._shared_required = opener is None
        self.opener = opener or urllib.request.build_opener(NoRedirect()).open
        self.token = None

    def http(self, source, endpoint, url, params=None, headers=None, body=None, auth=False):
        if source == 'KIS' and self._shared_required:
            from kis_shared.runtime import current
            from kis_shared.lease import Blocked
            try:current().before_http(auth=auth)
            except Blocked as exc:raise Failure(str(exc)) from None
        if params:
            url += '?' + urllib.parse.urlencode(params)
        entry = self.budget.before(source, endpoint, 'authentication' if auth else 'required_source')
        try:
            req = urllib.request.Request(url, headers=headers or {}, data=body)
            with self.opener(req, timeout=30) as r:
                raw = r.read(40_000_001)
                status = r.status
            if len(raw) > 40_000_000:
                raise Failure('response_size_exceeded')
            self.budget.finish(entry, 'received', http=status, response_hash=None if auth else sha(raw))
            return raw
        except urllib.error.HTTPError as e:
            self.budget.finish(entry, 'http_failure', http=e.code)
            raise Failure('authentication_failure' if e.code in (401,403) else 'http_'+str(e.code), e.code in (408,429) or e.code >= 500) from None
        except urllib.error.URLError as exc:
            denied='Tunnel connection failed: 403' in str(exc.reason)
            code='network_policy_denied' if denied else 'network_failure'
            self.budget.finish(entry, code)
            raise Failure(code, not denied) from None
        except (OSError, TimeoutError):
            self.budget.finish(entry, 'network_failure')
            raise Failure('network_failure', True) from None
        except Failure:
            self.budget.finish(entry, 'response_rejected')
            raise

    def kis(self, endpoint, params, tr):
        for name in CREDENTIALS['KIS']:
            if not os.getenv(name):
                raise Failure('missing_'+name)
        if self.token is None:
            raw = self.http('KIS', '/oauth2/tokenP', BASE+'/oauth2/tokenP',
                            headers={'Content-Type':'application/json'}, body=json.dumps(dict(grant_type='client_credentials', appkey=os.environ['KIS_APP_KEY'], appsecret=os.environ['KIS_APP_SECRET'])).encode(), auth=True)
            try:
                self.token = json.loads(raw)['access_token']
                if self._shared_required:
                    from kis_shared.runtime import current
                    current().token_response(json.loads(raw))
            except (ValueError, KeyError):
                raise Failure('authentication_failure') from None
        raw = self.http('KIS', endpoint, BASE+endpoint, params,
                        {'authorization':'Bearer '+self.token, 'appkey':os.environ['KIS_APP_KEY'], 'appsecret':os.environ['KIS_APP_SECRET'], 'custtype':'P', 'tr_id':tr})
        if self.token.encode() in raw:
            raise Failure('credential_echo')
        return raw

    def fetch(self, t):
        kind, day = t['kind'], t['asof'].replace('-', '')
        source, endpoint, params = None, None, {}
        if kind in ('flow','price','krx','index') and t['asof'] >= dt.datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat():
            raise Failure('post_session_reference_pending', True)
        if kind in ('flow','price','calendar','index'):
            source = 'KIS'
            if kind == 'calendar':
                endpoint, tr = Q+'chk-holiday', 'CTCA0903R'
                params = {'BASS_DT':day,'CTX_AREA_FK':'','CTX_AREA_NK':''}
            elif kind == 'flow':
                endpoint, tr = Q+'investor-trade-by-stock-daily', 'FHPTJ04160001'
                params = {'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':t['symbol'],'FID_INPUT_DATE_1':day,'FID_ORG_ADJ_PRC':'','FID_ETC_CLS_CODE':''}
            else:
                index = kind == 'index'
                endpoint, tr = (Q+'inquire-daily-indexchartprice','FHKUP03500100') if index else (Q+'inquire-daily-itemchartprice','FHKST03010100')
                params = {'FID_COND_MRKT_DIV_CODE':'U' if index else 'J','FID_INPUT_ISCD':('0001' if t['market']=='KOSPI' else '1001') if index else t['symbol'], 'FID_INPUT_DATE_1':(dt.date.fromisoformat(t['asof'])-dt.timedelta(days=60)).strftime('%Y%m%d'), 'FID_INPUT_DATE_2':day,'FID_PERIOD_DIV_CODE':'D'}
                # Original OHLCV is the existing auxiliary collection's verified option 1.
                # Never relabel it adjusted; no extra quote request or implicit basis merge.
                if not index: params['FID_ORG_ADJ_PRC']='1'
            raw = self.kis(endpoint, params, tr)
        elif kind == 'master':
            source='KIS_MASTER';endpoint=LAYOUTS[t['market']]['url']
            raw=self.http(source,endpoint,endpoint)
        elif kind == 'krx':
            source='KRX';endpoint='/svc/apis/sto/'+('stk' if t['market']=='KOSPI' else 'ksq')+'_bydd_trd'
            if not os.getenv('KRX_API_KEY'):raise Failure('missing_KRX_API_KEY')
            params={'basDd':day}
            raw=self.http(source,endpoint,'https://data-dbg.krx.co.kr'+endpoint,params,{'AUTH_KEY':os.environ['KRX_API_KEY']})
        elif kind in ('corp_codes','filings','financial'):
            source='DART'
            if not os.getenv('DART_API_KEY'):raise Failure('missing_DART_API_KEY')
            if kind=='corp_codes':endpoint='corpCode.xml'
            elif kind=='filings':
                endpoint='list.json';params={'corp_code':t['corp_code'],'bgn_de':t['asof'][:4]+'0101','end_de':day,'pblntf_ty':'A','last_reprt_at':'N','page_count':100,'page_no':t.get('page',1)}
            else:
                endpoint='fnlttSinglAcntAll.json'; f=t['filing']
                params={'corp_code':f['corp_code'],'bsns_year':str(f['year']),'reprt_code':f['report_code'],'fs_div':t['scope']}
            raw=self.http(source,endpoint,'https://opendart.fss.or.kr/api/'+endpoint,{'crtfc_key':os.environ['DART_API_KEY'],**params})
        elif kind=='macro':
            from screener.macro import catalog
            meta=catalog()[t['indicator']];source=meta['provider']
            if source=='FRED':
                endpoint='/graph/graph.csv';params={'id':meta['series'],'cosd':str(dt.date.fromisoformat(t['asof'])-dt.timedelta(days=65))}
                raw=self.http(source,endpoint,'https://fred.stlouisfed.org'+endpoint,params)
            elif source=='ECOS':
                if not os.getenv('ECOS_API_KEY'):raise Failure('missing_ECOS_API_KEY')
                stat,item=meta['series'].split('/'); start=dt.date.fromisoformat(t['asof'])-dt.timedelta(days=65)
                endpoint='StatisticSearch/'+stat+'/'+item  # sanitized identity; never persist key URL
                raw=self.http(source,endpoint,f'https://ecos.bok.or.kr/api/StatisticSearch/{os.environ["ECOS_API_KEY"]}/json/kr/1/100/{stat}/D/{start:%Y%m%d}/{day}/{item}/')
            else:raise Failure('use_index_task_for_KIS')
        else:raise Failure('unsupported_task')
        # Preserve even malformed API responses; parsing is a separate phase.
        evidence=self.store.preserve(raw, dict(task=t, source=source, endpoint=endpoint, params=params, collected_at=now(), source_run=self.budget.run))
        if source in ('KIS','DART') and kind not in ('corp_codes',):
            try: payload=json.loads(raw)
            except ValueError: raise Failure('response_schema_error') from None
            code=payload.get('msg_cd',payload.get('status'))
            if code in ('EGW00201','020'):raise Failure('provider_rate_limit',True)
            if source=='KIS' and payload.get('rt_cd')!='0':raise Failure('provider_response_rejected')
            if source=='DART' and payload.get('status') not in ('000','013'):raise Failure('DART_response_rejected')
        return evidence
