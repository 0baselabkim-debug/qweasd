"""Independent arithmetic against received KIS/KRX/DART values, not parser-derived expectations."""
import argparse,gzip,json,math,sys,collections
from pathlib import Path
import duckdb
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from screener.store import load
from screener.scoring import evaluate
ROOT=Path(__file__).resolve().parents[1]
def read(p):return json.loads(gzip.decompress(p.read_bytes()) if p.suffix=='.gz' else p.read_text())
ap=argparse.ArgumentParser();ap.add_argument('--raw',type=Path,required=True);ap.add_argument('--db',type=Path,default=ROOT/'data/dashboard.duckdb');args=ap.parse_args()
data=load(args.db);stocks,sectors,_=evaluate(data,'2026-10-14');stocks=stocks.set_index('symbol');out={'asof':'2026-10-14','stocks':[],'sectors':[]}
quotes_file=ROOT/'staging/kis_quotes_20261014.json.gz'
sample=read(args.raw/'kis_history_sample.json')
quotes=read(quotes_file)['records'] if quotes_file.exists() else {r['symbol']:r for r in sample['records']}
history_file=ROOT/'staging/kis_history_20261014.json.gz'
histories=read(history_file)['records'] if history_file.exists() else {r['symbol']:r for r in sample['records']}
original=read(args.raw.parent/'raw/kis_snapshot_20261014.json')
original_amounts={r['symbol']:r['foreign_net_amount_krw'] for r in original['rows']}
window={pd.strftime('%Y%m%d') for pd in sorted(data['calendar'].trade_date.unique())[-20:]}
independent_flow={}
for symbol,record in histories.items():
 amounts={r['stck_bsop_date']:int(r['frgn_ntby_tr_pbmn'])*1000000 for r in record['rows']}
 amounts['20261014']=original_amounts[symbol]
 if window.issubset(amounts) and symbol in quotes:
  values=[amounts[d] for d in window];cap=int(quotes[symbol]['quote']['hts_avls'])*100000000
  if cap>0:independent_flow[symbol]={'flow_intensity':sum(values)/cap,'flow_persistence':sum(v>0 for v in values)/20}
for code in ['005930','000660','035420']:
 p=next((args.raw/'dart_full').glob(code+'_CFS_2025.json.gz'));rows=read(p)['response']['list']
 def val(account,statement,key='thstrm_amount'):
  statements={'IS','CIS'} if statement=='IS' else {statement}
  values={int(r[key].replace(',','')) for r in rows if r['account_id']==account and r['sj_div'] in statements and r['account_detail']=='-' and r.get(key) not in ('','-',None)}
  assert len(values)==1,(code,account)
  return values.pop()
 revenue=val('ifrs-full_Revenue','IS');previous_revenue=val('ifrs-full_Revenue','IS','frmtrm_amount')
 op=val('dart_OperatingIncomeLoss','IS');prev_op=val('dart_OperatingIncomeLoss','IS','frmtrm_amount')
 ni=val('ifrs-full_ProfitLoss','IS');equity=val('ifrs-full_Equity','BS');prev_eq=val('ifrs-full_Equity','BS','frmtrm_amount')
 liabilities=val('ifrs-full_Liabilities','BS');ocf=val('ifrs-full_CashFlowsFromUsedInOperatingActivities','CF')
 cap=int(quotes[code]['quote']['hts_avls'])*100000000
 parent_ni=val('ifrs-full_ProfitLossAttributableToOwnersOfParent','IS');parent_eq=val('ifrs-full_EquityAttributableToOwnersOfParent','BS')
 expected={'revenue_growth':revenue/previous_revenue-1,'op_growth':op/prev_op-1,'roe':ni/((equity+prev_eq)/2),'debt_ratio':liabilities/equity,'ocf':ocf,'per':cap/parent_ni,'pbr':cap/parent_eq}
 expected.update(independent_flow[code])
 peer_codes=stocks[(stocks.market==stocks.loc[code,'market'])&(stocks.sector==stocks.loc[code,'sector'])].index
 peers=[independent_flow[c] for c in peer_codes if c in independent_flow]
 if len(peers)>=3:
  def rank(metric):
   value=independent_flow[code][metric]
   below=sum(p[metric]<value for p in peers);ties=sum(p[metric]==value for p in peers)
   return (below+(ties-1)/2)/(len(peers)-1)
  expected['score_foreign_flow']=(rank('flow_intensity')+rank('flow_persistence'))/2*30
 for key,value in expected.items():assert math.isclose(stocks.loc[code,key],value,rel_tol=1e-10),(code,key,stocks.loc[code,key],value)
 out['stocks'].append({'symbol':code,**expected})
with duckdb.connect(str(args.db),read_only=True) as c:
 for sector in ['전기·전자','IT 서비스']:
  amount,count=c.execute("SELECT SUM(f.net_amount),COUNT(*) FROM flows f JOIN memberships m USING(symbol) WHERE f.trade_date='2026-10-14' AND m.market='KOSPI' AND m.sector=? AND m.valid_from<=f.trade_date AND (m.valid_to IS NULL OR m.valid_to>=f.trade_date)",[sector]).fetchone()
  s=sectors[(sectors.market=='KOSPI')&(sectors.sector==sector)].iloc[0];assert amount==s.net_amount and count==s.members
  out['sectors'].append({'market':'KOSPI','sector':sector,'net_amount_krw':amount,'members':count})
 assert c.execute("SELECT COUNT(*) FROM financials WHERE disclosed_on>'2026-10-14' OR currency!='KRW' OR NOT disclosure_verified").fetchone()[0]==0
 assert c.execute("SELECT COUNT(*) FROM (SELECT symbol,trade_date,COUNT(*) n FROM flows GROUP BY 1,2 HAVING n>1)").fetchone()[0]==0
original=read(args.raw.parent/'raw/kis_snapshot_20261014.json')
assert len(original['rows'])==2430
for r in original['rows']:
 s=stocks.loc[r['symbol']];assert s.net_amount==r['foreign_net_amount_krw'] and s.net_quantity==r['foreign_net_quantity_shares']
# Independent turnover/close/volume comparison for actual historical controls.
prices={(r.symbol,r.trade_date.strftime('%Y%m%d')):r for r in data['prices'].itertuples()}
price_checks=0;amount_rounding=collections.Counter();halted_close_differences=[]
for record in sample['records']:
 for r in record['rows']:
  if (record['symbol'],r['stck_bsop_date']) in prices:
   p=prices[(record['symbol'],r['stck_bsop_date'])]
   assert (int(r['acml_vol']),int(r['acml_tr_pbmn']))==(p.volume,p.turnover)
   if int(r['stck_clpr'])!=p.close:
    assert p.volume==0,('active_close_mismatch',record['symbol'],r['stck_bsop_date'])
    halted_close_differences.append({'symbol':record['symbol'],'date':r['stck_bsop_date'],'kis_close':int(r['stck_clpr']),'krx_close':p.close,'volume':0,'action':'Retain official KRX close; no price return computed from this KIS field'})
   price_checks+=1
  if all(k in r for k in ['frgn_shnu_tr_pbmn','frgn_seln_tr_pbmn','frgn_ntby_tr_pbmn']):
   diff=int(r['frgn_shnu_tr_pbmn'])-int(r['frgn_seln_tr_pbmn'])-int(r['frgn_ntby_tr_pbmn']);amount_rounding[diff]+=1
out.update(original_rows_preserved=2430,historical_price_crosschecks=price_checks,halted_close_differences=halted_close_differences,amount_buy_minus_sell_minus_reported_million_krw=dict(amount_rounding),future_disclosures=0,duplicate_flow_keys=0)
(ROOT/'reports/enrichment/independent_checks.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(out,ensure_ascii=False,indent=2))
