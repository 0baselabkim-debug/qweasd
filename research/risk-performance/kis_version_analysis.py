"""Offline A/B/C observation comparison; never mutates price or signal stores."""
import csv,datetime as dt,gzip,hashlib,json,subprocess,math
from pathlib import Path
from kis_version_probe import OUT,TARGETS,START,END
from official_probe import save,sha,now
ROOT=Path(__file__).resolve().parent;REPO=ROOT.parents[1]
FIELDS=['open','high','low','close','volume','turnover']
KIS=['stck_oprc','stck_hgpr','stck_lwpr','stck_clpr','acml_vol','acml_tr_pbmn']
KRX=['TDD_OPNPRC','TDD_HGPRC','TDD_LWPRC','TDD_CLSPRC','ACC_TRDVOL','ACC_TRDVAL']
def load(p):return json.loads(gzip.decompress(p.read_bytes()) if p.suffix=='.gz' else p.read_bytes())
def values(r,keys):return dict(zip(FIELDS,[float(r[k]) for k in keys]))
def validate_new(entry,payload,symbol,kind):
 p=entry.get('params',{})
 expected={'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':symbol,'FID_INPUT_DATE_1':START,'FID_INPUT_DATE_2':END,'FID_PERIOD_DIV_CODE':'D','FID_ORG_ADJ_PRC':kind}
 if p!=expected or entry.get('endpoint')!='https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice':raise ValueError('request_identity_or_scope')
 if entry.get('http')!=200 or payload.get('rt_cd')!='0' or payload.get('output1',{}).get('stck_shrn_iscd')!=symbol:raise ValueError('response_failed_or_wrong_symbol')
 rows=payload.get('output2',[]);dates=[r.get('stck_bsop_date') for r in rows]
 if not dates or any(not d or not START<=d<=END for d in dates) or len(dates)!=len(set(dates)):raise ValueError('response_dates_invalid')
 selected=[r for r in rows if r['stck_bsop_date']==END]
 if len(selected)!=1:raise ValueError('exact_20261020_row_missing')
 b=values(selected[0],KIS)
 if not(all(math.isfinite(v) for v in b.values()) and 0<b['low']<=min(b['open'],b['close'])<=max(b['open'],b['close'])<=b['high'] and b['volume']>=0 and b['turnover']>=0):raise ValueError('invalid_ohlcv')
 return b,{k:selected[0].get(k) for k in ['prtt_rate','revl_issu_reas','mod_yn','flng_cls_code']}
def classify(original,official,adjusted,raw):
 if adjusted is None or raw is None:return '6_검증불가'
 a=adjusted==official;b=raw==official
 if a and b:return '1_수정_원주가_모두_KRX일치'
 if a:return '2_수정주가만_KRX일치'
 if b:return '3_원주가만_KRX일치'
 if adjusted!=raw:return '4_수정_원주가_서로다름_둘다불일치'
 if adjusted==original:return '5_최초관측그대로_KRX불일치'
 return '추가유형_두옵션동일하게변경_둘다KRX불일치'
def main():
 OUT.mkdir(exist_ok=True);inputs={}
 def source(p):inputs[str(p.relative_to(REPO))]=sha(p.read_bytes());return inputs[str(p.relative_to(REPO))]
 ledger_path=OUT/'API_EXECUTION.json'
 ledger=load(ledger_path) if ledger_path.exists() else {'authorization':'kis-two-price-versions-after-20261021-2300','maximum':5,'requests':[],'checks':[],'status':'not_started_before_23','created_at':now()}
 if not ledger_path.exists():save(ledger_path,ledger)
 assert ledger['maximum']==5 and len(ledger['requests'])<=5;source(ledger_path)
 oldpath=ROOT.parent/'full-market/inputs/checkpoint-20261020.json.gz';cp=load(oldpath);oldsha=source(oldpath)
 reference=ROOT/'official-crosscheck/references/KOSPI-2026-10-20.json';ref=load(reference);refsha=source(reference)
 assert ref['http']==200 and ref['url']=='https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd?basDd=20261020'
 # Verify against immutable HTTP bytes saved by the earlier approved probe.
 previous=load(ROOT/'official-crosscheck/API_EXECUTION.json');entry=next(r for r in previous['requests'] if r['key']=='KOSPI-20261020');path=ROOT/'official-crosscheck'/entry['file'];source(path)
 raw_bytes=gzip.decompress(path.read_bytes());assert sha(raw_bytes)==entry['response_sha256'];assert json.loads(raw_bytes)['OutBlock_1']==ref['rows']
 cases=[];table=[]
 for symbol,name in [('095570','AJ네트웍스'),('006840','AK홀딩스')]:
  r=cp['records'][symbol];oldrow=next(v for v in r['adjusted_prices'] if v['stck_bsop_date']==END)
  assert r['flow']['stck_bsop_date']==END and r['request_scope']=={'market':'J','symbol':symbol,'end':END,'adjustment':'0'}
  rows=[v for v in ref['rows'] if v['ISU_CD']==symbol and v['BAS_DD']==END and v['MKT_NM']=='KOSPI'];assert len(rows)==1
  old=values(oldrow,KIS);official=values(rows[0],KRX)
  versions=[('A_investor_blank',values(r['flow'],KIS),r['observed_at'],oldsha,{'market':'J','date':END,'adjustment':''}),('A_adjusted_0',old,r['observed_at'],oldsha,r['request_scope']),('B_KRX_official',official,ref['observed_at'],entry['response_sha256'],{'basDd':END})]
  new={};errors={};flags={}
  for kind in ['0','1']:
   match=next((x for x in ledger['requests'] if x['key']==symbol+'-'+kind),None);b=None;time=None;h=None;params=None
   if match and match.get('file'):
    path=OUT/match['file'];assert source(path)==match['file_sha256'];raw_bytes=gzip.decompress(path.read_bytes());assert sha(raw_bytes)==match['response_sha256']
    try:b,flags[kind]=validate_new(match,json.loads(raw_bytes),symbol,kind)
    except (ValueError,KeyError,TypeError) as e:errors[kind]=str(e)
    time=match['completed_at'];h=match['response_sha256'];params=match['params']
   else:errors[kind]='not_requested_or_no_response'
   new[kind]=b;versions.append(('C_adjusted_0' if kind=='0' else 'C_raw_1',b,time,h,params))
  state=classify(old,official,new['0'],new['1'])
  cases.append({'symbol':symbol,'name':name,'classification':state,'old_adjusted':old,'official':official,'new_adjusted':new['0'],'new_raw':new['1'],'errors':errors,'adjustment_flags':flags,'signal_registered':False})
  for label,b,t,h,params in versions:
   for k in FIELDS:table.append({'symbol':symbol,'name':name,'trade_date':'2026-10-20','version':label,'field':k,'value':b[k] if b else None,'difference_vs_old_adjusted':b[k]-old[k] if b else None,'difference_vs_KRX':b[k]-official[k] if b else None,'six_fields_match_KRX':b==official if b else None,'observed_at':t,'source_sha256':h,'request_parameters':json.dumps(params,ensure_ascii=False) if params else None,'status':'observed' if b else 'not_observed'})
 with (OUT/'PRICE_COMPARISON.csv').open('w',encoding='utf-8-sig',newline='') as f:w=csv.DictWriter(f,fieldnames=list(table[0]));w.writeheader();w.writerows(table)
 source(ROOT/'official-crosscheck/updated-coverage.csv');save(OUT/'results.json',{'asof':'2026-10-20','cases':cases,'actual_new_signals':0,'requests':sum(r.get('physical_request',False) for r in ledger['requests']),'data_missing_is_not_zero_candidates':True,'previous189_status_unchanged':True})
 save(OUT/'input-hashes.json',inputs)
 n=sum(r.get('physical_request',False) for r in ledger['requests']);lines=['# KIS 가격 버전 비교','',f'생성 시각: {now()}. **실제 시장 요청 {n}/5회, 실제 신규 신호 0건.**','',('23:00 이전 실행 제한으로 신규 재조회는 미실행이다. 아래 C 값은 결측으로 보존하며 이전 조회를 이번 재조회로 재사용하지 않는다.' if not n else '인증/실패 포함 누적 원장은 API_EXECUTION.json. HTTP 성공과 날짜·종목·6필드 검증을 분리한다.'),'','| 종목 | 재조회 판정 |','|---|---|']
 for c in cases:lines.append(f"|{c['name']}({c['symbol']})|{c['classification']}|")
 lines+=['','## 기존 값과 공식 값(보존 자료)','', '|종목|버전|시가|고가|저가|종가|거래량|거래대금|','|---|---|---:|---:|---:|---:|---:|---:|']
 for c in cases:
  for key in ['old_adjusted','official','new_adjusted','new_raw']:
   b=c[key];lines.append('|'+c['name']+'|'+key+'|'+'|'.join(str(b[k]) if b else '미조회' for k in FIELDS)+'|')
 lines+=['','각 필드의 차이·수신 시각·파라미터·해시는 PRICE_COMPARISON.csv에 있다.','0/1 요청은 직렬 실행되므로 동일 순간의 관측이 아니다. 옵션 차이가 나타나도 조회 간 갱신과 기업행사 조정 원인을 자동 확정하지 않는다.','기존 189개/보류 종목을 갱신하지 않았으며, 10/20 신호를 등록하지 않는다. 신규 기준일 전체 요건 검증은 이번 두 종목 과거 조회로 대체할 수 없다.']
 (OUT/'KIS_PRICE_VERSION_REPORT.md').write_text('\n'.join(lines)+'\n')
 hypotheses=['# 원인 가설 판정','', '|가설|증거에 따른 판정|한계|','|---|---|---|']
 for c in cases:
  a=c['new_adjusted'];b=c['new_raw'];o=c['old_adjusted'];official=c['official']
  text='판정 불가: 신규 응답 미확보' if a is None or b is None else ('지지: 후속 가격이 변경되어 공식값과 일치' if (a!=o and a==official) or (b!=o and b==official) else '이번 표본에서 공식값 방향 사후 변경 미확인')
  hypotheses.append(f"|A 사후 갱신 / {c['name']}|{text}|변경 이유·정확한 확정 시각은 증명하지 않음|")
  text='판정 불가: 신규 응답 미확보' if a is None or b is None else ('옵션 차이 관측; 기업행사 원인 판정 불가' if a!=b else '수정/원 옵션 차이 설명은 이번 날짜 표본에서 지지되지 않음')
  hypotheses.append(f"|B 조정 옵션 / {c['name']}|{text}|전 기간 기업행사 부재를 증명하는 것은 아님|")
 hypotheses += ['|C 시장·시간외 범위|판정 불가|보존 공식 예제는 J=KRX, NX=NXT, UN=통합을 명시하지만 필드별 시간외/확정 정책 근거는 없음|','|D 저장 버전 혼합|코드 경로 및 기존 31종목 근거로 확인|수집 전 원시 KIS 자체의 차이를 설명하지는 못함|','','`screener/daily.py`는 기존 close/volume/turnover를 보존하고 이력 수신 시 adjusted_close만 UPDATE한다. 수정종가 시계열은 후속 버전, 거래량·거래대금은 이전 버전인 행이 생길 수 있다. 기존 31종목 공식 대조가 실제 사례다. 운영 코드는 이번에 수정하지 않았다.','종가 변경은 MA와 스윙 위치·돌파 날짜를 바꿀 수 있고, 거래량·거래대금만 변경되면 종가 기반 신호는 직접 변하지 않지만 유동성·거래 활성도는 달라질 수 있다. 이 문장은 구조적 영향이며 실제 신호 변화량을 측정했다는 주장이 아니다.','10/16 후속 2,156개 일치와 10/20 불일치 2,198개는 사후 관측 버전 문제의 조사 근거다. 이번 두 종목 결과를 나머지 2,196개에 확장하지 않는다.']
 (OUT/'ROOT_CAUSE_ASSESSMENT.md').write_text('\n'.join(hypotheses)+'\n')
 print(json.dumps({'physical_requests':n,'cases':[c['classification'] for c in cases],'actual_registered':0}))
if __name__=='__main__':main()
