"""Public observation persistence and KIS J response parsing."""
import datetime as dt,gzip,json,math
from pathlib import Path
from screener.liquidity_quality import digest
ROOT=Path("liquidity");CACHE=ROOT/"candidates"
FIELDS={'open': 'stck_oprc', 'high': 'stck_hgpr', 'low': 'stck_lwpr', 'close': 'stck_clpr', 'volume': 'acml_vol', 'turnover': 'acml_tr_pbmn'}
def read(path,default=None):
    try:
        raw=path.read_bytes();return json.loads(gzip.decompress(raw) if path.suffix=='.gz' else raw)
    except (OSError,ValueError,EOFError):return default

def chart(api,symbol,start,end,market='J',adjustment='0'):
    return api.request('/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice',{'FID_COND_MRKT_DIV_CODE':market,'FID_INPUT_ISCD':symbol,'FID_INPUT_DATE_1':start.replace('-',''),'FID_INPUT_DATE_2':end.replace('-',''),'FID_PERIOD_DIV_CODE':'D','FID_ORG_ADJ_PRC':adjustment},'FHKST03010100')

def parsed(rows,asof):
    result={}
    for r in rows:
        if not r.get('stck_bsop_date'):continue
        day=dt.datetime.strptime(r['stck_bsop_date'],'%Y%m%d').date().isoformat()
        if day>asof:continue
        if day in result:raise ValueError('duplicate_date')
        bar={'trade_date':day,**{k:float(r[v]) for k,v in FIELDS.items()}}
        if not all(math.isfinite(bar[k]) for k in FIELDS):raise ValueError('nonfinite_value')
        result[day]={'bar':bar,'event':{'trade_date':day,'rate':r.get('prtt_rate'),'reason':r.get('revl_issu_reas')}}
    return result

def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    raw=json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
    if path.suffix=='.gz':raw=gzip.compress(raw,mtime=0)
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_bytes(raw);tmp.replace(path)
