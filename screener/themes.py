"""Evidence-backed multi-label themes. The union is always deduplicated by symbol."""
import json
from pathlib import Path
import pandas as pd

PATH=Path(__file__).resolve().parents[1]/'theme_mapping.json'

def definitions():
    return json.loads(PATH.read_text())

def members(asof, mapping=None):
    mapping=mapping or definitions();day=pd.Timestamp(asof)
    records=[r for r in mapping['memberships'] if r['status']=='confirmed'
             and pd.Timestamp(r['valid_from'])<=day
             and (not r.get('valid_to') or pd.Timestamp(r['valid_to'])>=day)
             and pd.Timestamp(r['disclosed_on'])<=day and r.get('evidence_url') and r.get('excerpt')]
    return pd.DataFrame(records).drop_duplicates(['symbol','theme']) if records else pd.DataFrame(columns=['symbol','theme'])

def aggregate(stocks, asof, mapping=None):
    mapping=mapping or definitions();m=members(asof,mapping)
    # An official-sector stock row remains unique even if it belongs to two themes.
    if stocks.symbol.duplicated().any():raise ValueError('duplicate_stock_rows')
    selected=stocks.merge(m[['symbol','theme']],on='symbol',how='inner',validate='one_to_many')
    rows=[]
    for theme in mapping['themes']:
        group=selected[selected.theme==theme['name']].drop_duplicates('symbol')
        rows.append({'sector':theme['name'],'members':len(group),'net_amount':group.net_amount.sum(min_count=1),
                     'market':'테마','relative_strength':float('nan'),'total_score':float('nan'),'evaluation_rank':float('nan')})
    unique=selected.drop_duplicates('symbol')
    return pd.DataFrame(rows),selected,{'unique_members':len(unique),'unique_net_amount':unique.net_amount.sum(min_count=1),
                                     'membership_count':len(selected),'overlap_count':len(selected)-len(unique)}
