"""Offline evidence review, source hashes and observations remain untouched."""
from pathlib import Path
from html.parser import HTMLParser
import json,gzip,hashlib,zipfile
import pandas as pd
ROOT=Path(__file__).resolve().parent
FIELDS=['open','high','low','close','volume','turnover'];KIS=['stck_oprc','stck_hgpr','stck_lwpr','stck_clpr','acml_vol','acml_tr_pbmn'];KRX=['TDD_OPNPRC','TDD_HGPRC','TDD_LWPRC','TDD_CLSPRC','ACC_TRDVOL','ACC_TRDVAL']
def read(p):return json.loads(gzip.decompress(p.read_bytes()) if p.suffix=='.gz' else p.read_bytes())
def main():
 out=ROOT/'evidence';out.mkdir(exist_ok=True);refs={};sources=[]
 for o in read(ROOT/'collection/official-reference-probe.json')['records']:
  p=ROOT/'collection'/o['file'];assert hashlib.sha256(p.read_bytes()).hexdigest()==o['sha256'];x=read(p);sources.append({'file':str(p.relative_to(ROOT)),'sha256':o['sha256']})
  refs.update({r['ISU_CD']:r for r in x['response']['OutBlock_1']})
 cp=read(ROOT/'inputs/checkpoint-20261019.json.gz')['records'];cp20=read(ROOT/'inputs/checkpoint-20261020.json.gz')['records'];pilot=read(ROOT/'inputs/pilot/run.json')['records'];new=read(ROOT/'collection/state.json')['records'];universe=pd.read_csv(ROOT/'inputs/baseline-screening.csv',dtype={'symbol':str});rows=[]
 for stock in universe.itertuples():
  code=stock.symbol;r=refs.get(code);assert r and r['BAS_DD']=='20261019'
  old=next(x for x in cp[code]['adjusted_prices'] if x['stck_bsop_date']=='20261019');later=next((x for x in cp20.get(code,{}).get('adjusted_prices',[]) if x['stck_bsop_date']=='20261019'),None)
  entry=pilot.get(code) or new.get(code);pair={}
  for kind,e in (entry or {}).items():
   p=ROOT/'inputs/pilot'/e['file'] if code in pilot else ROOT/'collection'/e['file'];pair[kind]=next(x for x in read(p)['output2'] if x['stck_bsop_date']=='20261019')
  a={'symbol':code,'name':stock.name,'market':stock.market,'asof':'2026-10-19','original_matches_KRX':all(int(old[k])==int(r[v]) for k,v in zip(KIS,KRX)),'later_checkpoint_matches_KRX':all(int(later[k])==int(r[v]) for k,v in zip(KIS,KRX)) if later else None}
  for label,d in [('original',old),('later_checkpoint',later),*pair.items()]:
   if d is None:continue
   a[label+'_mismatch_fields']=','.join(f for f,k,v in zip(FIELDS,KIS,KRX) if int(d[k])!=int(r[v]))
   for f,k in zip(FIELDS,KIS):a[label+'_'+f]=int(d[k])
  for f,k in zip(FIELDS,KRX):a['KRX_'+f]=int(r[k])
  rows.append(a)
 f=pd.DataFrame(rows);f.to_csv(out/'official-20261019-comparison.csv',index=False,encoding='utf-8-sig')
 eight=pd.read_csv(ROOT/'inputs/eight-price-comparison.csv',dtype={'symbol':str});f[f.symbol.isin(eight.symbol)].to_csv(out/'previous-eight-official-comparison.csv',index=False,encoding='utf-8-sig')
 # Verify the exchange-issued notice itself, not only its filing title.
 action=read(ROOT/'collection/sangji-official-probe.json');o=next(x for x in action['observations'] if x.get('receipt')=='20260921900481');p=ROOT/'collection'/o['file'];assert hashlib.sha256(p.read_bytes()).hexdigest()==o['sha256']
 class Cells(HTMLParser):
  def __init__(self):super().__init__();self.active=False;self.cells=[];self.parts=[]
  def handle_starttag(self,tag,attrs):
   if tag=='td':self.active=True;self.parts=[]
  def handle_data(self,text):
   if self.active:self.parts.append(text)
  def handle_endtag(self,tag):
   if tag=='td':self.cells.append(' '.join(''.join(self.parts).split()));self.active=False
 with zipfile.ZipFile(p) as z:
  assert z.namelist()==['20260921900481.xml'];raw=z.read(z.namelist()[0]);parser=Cells()
  try:text=raw.decode('utf-8');encoding='utf-8'
  except UnicodeDecodeError:text=raw.decode('euc-kr');encoding='euc-kr'
  parser.feed(text)
 expected=['상지건설','보통주식','A042940','5,680','2026-09-22','유상증자'];assert parser.cells[-6:]==expected
 pairs={k:{r['stck_bsop_date']:r for r in read(ROOT/'inputs/pilot'/e['file'])['output2']} for k,e in pilot['042940'].items()}
 prior=int(pairs['raw']['20260921']['stck_clpr']);assert prior==5900
 price_diffs=[];volume_diffs=[];price_checks=0
 for d in sorted(pairs['raw']):
  adjusted=pairs['adjusted'][d];raw=pairs['raw'][d]
  assert adjusted['acml_tr_pbmn']==raw['acml_tr_pbmn']
  if d<'20260922':
   for k in KIS[:4]:
    price_checks+=1;expected_price=int(raw[k])*5680//prior
    if int(adjusted[k])!=expected_price:price_diffs.append({'date':d,'field':k,'expected':expected_price,'observed':int(adjusted[k])})
   expected_volume=int(raw['acml_vol'])*prior//5680
   if int(adjusted['acml_vol'])!=expected_volume:volume_diffs.append({'date':d,'expected':expected_volume,'observed':int(adjusted['acml_vol'])})
  else:assert all(adjusted[k]==raw[k] for k in KIS)
 review={'symbol':'042940','event_confirmed':True,'event':'유상증자 권리락','effective_date':'2026-09-22','official_base_price':5680,'official_receipt':'20260921900481','official_url':o['public_url'],'notice_source_file':str(p.relative_to(ROOT)),'notice_sha256':o['sha256'],'decoded_encoding':encoding,'prior_raw_close_KIS':prior,'prior_close_KRX_crosscheck':False,'price_ratio':5680/prior,'observational_rounding_model':'floor(raw_price*5680/5900)','rounding_model_officially_documented':False,'price_field_checks':price_checks,'price_field_differences':price_diffs,'volume_rounding_differences':volume_diffs,'turnover_equal_all':True,'post_effective_OHLCV_equal':True,'signal_quality_status':'보류: 공급자 정밀도 규칙 미확인; 기업행사 자체는 확인됨'}
 (out/'sangji-review.json').write_text(json.dumps(review,ensure_ascii=False,indent=2)+'\n')
 (out/'reference-summary.json').write_text(json.dumps({'stocks':len(f),'old_full_six_match':int(f.original_matches_KRX.sum()),'later_checkpoint_full_six_match':int(f.later_checkpoint_matches_KRX.sum()),'sources':sources},ensure_ascii=False,indent=2)+'\n')
 print('Official comparison:',len(f),'stocks; Sangji event confirmed; price checks',price_checks,'volume precision exceptions',len(volume_diffs))
if __name__=='__main__':main()
