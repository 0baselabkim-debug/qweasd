"""Presentation and navigation only; no changes to data, analysis or eligibility."""
import html,json
from pathlib import Path
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
_table=components.declare_component('flow_explorer',path=str(Path(__file__).parent/'components/flow_table'))

def table(frame,ids,name_column,key,storage_key):
    records=json.loads(frame.to_json(orient='values',force_ascii=False))
    if len(ids)!=len(records) or len(set(ids))!=len(ids):raise ValueError('Table row IDs must be unique')
    return _table(columns=list(frame.columns),rows=[{'id':i,'values':v} for i,v in zip(ids,records)],name_column=name_column,storage_key=storage_key,key=key,default=None)

def navigate(state,action,value=None):
    def assign(**values):
        for k,v in values.items():state[k]=v
    if action=='sector':assign(group=tuple(value),symbol=None,sector_open=True,stock_open=False,stage='stocks')
    elif action=='stock':assign(symbol=value,stock_open=True,stage='detail')
    elif action=='close_sector':assign(sector_open=False,stock_open=False,symbol=None,stage='sectors')
    elif action=='close_stock':
        assign(stock_open=False,symbol=None)
        if state['stage']=='detail':state['stage']='stocks'
    else:state['stage']=action
    state['nav_epoch']+=1

def macro_rows(state,catalog,group):
    rows=[]
    for key,meta in catalog.items():
        if meta['group']!=group:continue
        r=state.get('indicators',{}).get(key)
        value='미확보';change='미확보'
        if r:
            unit='달러/배럴' if key=='wti' else r['unit']
            value=f"{r['value']:,.3f}" if meta['rate'] else f"{r['value']:,.2f}"
            value+=(' '+unit if unit!='포인트' else '')
            if meta['rate']:change=f"{r['change']:+.2f}bp ({r['change']/100:+.3f}%p)"
            else:
                delta=f"{r['change']:+,.2f}"+('pt' if unit=='포인트' else unit)
                change=(f"{r['change_percent']:+.2f}% ({delta})" if r.get('change_percent') is not None else delta)
        rows.append({'항목명':meta['name'],'최신 데이터 값':value,'전일 대비':change,'date':r.get('date') if r else None,'change':r.get('change') if r else None,'provider':r.get('provider') if r else meta['provider']})
    return rows

def macro_card(title,rows):
    body=[]
    for row in rows:
        tone='positive' if row['change'] is not None and row['change']>0 else 'negative' if row['change'] is not None and row['change']<0 else ''
        provenance=f"기준일 {row['date'] or '미확보'} · {row['provider']}"
        body.append('<tr title="'+html.escape(provenance,quote=True)+'"><td>'+html.escape(row['항목명'])+'</td><td class="num">'+html.escape(row['최신 데이터 값'])+'</td><td class="num '+tone+'">'+html.escape(row['전일 대비'])+'</td></tr>')
    dates=sorted({r['date'] for r in rows if r['date']})
    # Small provenance range prevents lagged publications being mistaken for today's close.
    stamp='공표 기준 '+(' ~ '.join([dates[0],dates[-1]]) if len(dates)>1 else dates[0] if dates else '미확보')
    st.markdown('<section class="macro-card"><div class="macro-card-head"><h3>'+html.escape(title)+'</h3><span>'+stamp+'</span></div><div class="mini-scroll"><table class="mini-table macro-table"><thead><tr><th>항목명</th><th class="num">최신 데이터 값</th><th class="num">전일 대비</th></tr></thead><tbody>'+''.join(body)+'</tbody></table></div></section>',unsafe_allow_html=True)

CSS='''<style>
[data-testid="stSidebarHeader"],[data-testid="stSidebarCollapseButton"],[data-testid="stExpandSidebarButton"],[data-testid="stSidebarCollapsedControl"],[data-testid="stSidebar"] [data-testid="stLogoSpacer"]{display:none!important}
[data-testid="stSidebar"]{display:block!important;visibility:visible!important;position:relative!important;transform:none!important;width:270px!important;min-width:270px!important;max-width:270px!important;flex-shrink:0!important;height:100dvh!important;margin-left:0!important;border-right:1px solid #28405f}
[data-testid="stSidebarContent"]{overflow-y:auto!important;height:100%!important;padding-top:0!important}
[data-testid="stSidebarUserContent"]>div{padding:0!important}[data-testid="stSidebarUserContent"]{padding:1.4rem 1rem 2rem!important}
[data-testid="stMain"]{min-width:0!important;position:relative!important}.block-container{padding-top:1.3rem!important}
.sidebar-title{font-size:22px;font-weight:780;line-height:1.4;letter-spacing:-.06em;margin:7px 0 20px;color:#f1f6ff}
.st-key-sidebar_menu{gap:10px!important}.st-key-sidebar_menu button{min-height:46px!important;justify-content:flex-start!important;padding-left:16px!important;font-size:15px!important;font-weight:650!important}
.st-key-sidebar_menu button[kind="primary"],.st-key-hierarchy button[kind="primary"]{background:linear-gradient(115deg,#2778ff,#1d58e9)!important;border-color:#3d80ff!important;color:white!important}
[data-testid="stSidebar"] hr{margin:18px 0!important;border-color:#233b5a!important}
.date-label{height:30px!important;justify-content:flex-start!important;gap:5px;font-size:12px!important}
.st-key-header{margin-bottom:4px}.st-key-header [data-testid="stLinkButton"] a{font-size:12px!important;min-height:30px!important}
.st-key-hierarchy{gap:7px!important;padding:5px 0 10px;border-bottom:1px solid #264772;margin-bottom:8px}
.st-key-hierarchy_sector,.st-key-hierarchy_stock{gap:0!important;align-items:center!important;border:1px solid #2a4a71;border-radius:7px;background:#0d1c31}
.st-key-hierarchy_sector button,.st-key-hierarchy_stock button{border:0!important;box-shadow:none!important}
.st-key-close_sector,.st-key-close_stock{opacity:0;transition:opacity .15s;width:30px!important;flex-shrink:0}
.st-key-close_sector button,.st-key-close_stock button{min-width:28px;padding:2px 5px!important;color:#9bbce9!important}
.st-key-hierarchy_sector:hover .st-key-close_sector,.st-key-hierarchy_sector:focus-within .st-key-close_sector,.st-key-hierarchy_stock:hover .st-key-close_stock,.st-key-hierarchy_stock:focus-within .st-key-close_stock{opacity:1}
.macro-card{background:#0d192a;border:1px solid #243d5d;border-radius:9px;padding:12px;margin-bottom:4px;overflow:hidden}.macro-card-head{display:flex;justify-content:space-between;gap:8px;align-items:baseline;margin-bottom:8px;flex-wrap:wrap}.macro-card h3{margin:0!important;padding:0!important;font-size:15px!important}.macro-card-head span{font-size:10px;color:#90a9cc}.macro-table th{font-size:11px;padding:7px 8px}.macro-table td{font-size:12px;padding:8px}.macro-table td:first-child{white-space:normal;min-width:95px}.macro-table .num{white-space:nowrap}.macro-card .mini-scroll{border:0}.investor-table{max-width:700px}.investor-table td{padding:6px 10px!important}
@media(max-width:1180px){.st-key-stock_kpis [data-testid="stHorizontalBlock"],.st-key-detail_metrics [data-testid="stHorizontalBlock"]{display:grid!important;grid-template-columns:repeat(2,minmax(0,1fr))!important;gap:8px!important}.st-key-stock_kpis [data-testid="stColumn"],.st-key-detail_metrics [data-testid="stColumn"]{width:100%!important;min-width:0!important}.st-key-detail_metrics [data-testid="stColumn"]:last-child{grid-column:1/-1}.st-key-detail_metrics [data-testid="stMetricValue"]{font-size:27px!important}.st-key-detail_metrics [data-testid="stMetricValue"]>div{white-space:normal!important;overflow:visible!important;text-overflow:clip!important}.st-key-macro_cards>[data-testid="stHorizontalBlock"]{flex-direction:column!important}.st-key-macro_cards>[data-testid="stHorizontalBlock"]>[data-testid="stColumn"]{width:100%!important;flex:1 1 100%!important}}
@media(max-width:760px){[data-testid="stAppViewContainer"]{flex-direction:column!important;overflow-y:auto!important}[data-testid="stSidebar"]{width:100%!important;min-width:100%!important;max-width:100%!important;height:290px!important;min-height:290px!important;max-height:290px!important;position:relative!important;border-right:0;border-bottom:1px solid #28405f}[data-testid="stSidebarUserContent"]>div{padding:0!important}[data-testid="stSidebarUserContent"]{padding:.6rem .9rem 1rem!important}.sidebar-title{font-size:21px;margin:2px 0 10px}[data-testid="stMain"]{position:relative!important;height:auto!important;overflow:visible!important;flex:1 0 auto!important;width:100%!important}[data-testid="stAppViewContainer"]>div:has(>[data-testid="stMain"]){flex:1 0 auto!important;width:100%!important}.st-key-close_sector,.st-key-close_stock{opacity:1}.st-key-hierarchy{flex-wrap:wrap!important}.macro-card{padding:9px}.macro-table td{font-size:11px;padding:8px 6px}.macro-card-head span{font-size:10px}}
@media(pointer:coarse){.st-key-close_sector,.st-key-close_stock{opacity:1}}
</style>'''
def install():st.markdown(CSS,unsafe_allow_html=True)

READABILITY_CSS='''
[data-testid="stMain"] .block-container{width:100%!important;max-width:none!important;margin-left:0!important;margin-right:auto!important}
.macro-card{padding:14px;margin-bottom:12px}.macro-card-head{margin-bottom:12px}.macro-card h3{font-size:16px!important}.macro-card-head span{font-size:11px}
.st-key-macro_cards>[data-testid="stHorizontalBlock"]{gap:22px!important}
.macro-card .mini-scroll,.investor-table{width:fit-content;max-width:100%}
.macro-table,.investor-table .mini-table{width:max-content;min-width:0;table-layout:auto}
.macro-table th,.investor-table th{height:44px;padding:10px;font-size:14px;line-height:22px}
.macro-table td,.investor-table td{height:46px;padding:11px 10px!important;font-size:14px!important;line-height:23px;vertical-align:middle}
.macro-table td:first-child{min-width:0;white-space:nowrap}
.st-key-stock_kpis{margin:6px 0 16px}.kpi{height:96px;padding:14px 16px}.kpi-label{font-size:13px;white-space:normal}.kpi-value{white-space:normal;overflow-wrap:break-word}
.st-key-detail_metrics{margin:16px 0 20px}.st-key-detail_metrics [data-testid="stMetricValue"]>div{white-space:normal!important;overflow:visible!important;text-overflow:clip!important}
@media(min-width:1360px){[data-testid="stMain"] .block-container{width:min(100%,max(var(--reading-min),var(--reading-width)))!important}}
@media(max-width:1180px){.macro-card{padding:12px}.kpi{height:auto;min-height:96px}.kpi-label{font-size:13px}.st-key-stock_kpis [data-testid="stHorizontalBlock"]{gap:12px!important}}
'''

def reading_layout(stage):
    """Only the presentation viewport varies by current page; state/navigation is unchanged."""
    percent,minimum={'macro':('70%','1050px'),'sectors':('85%','980px'),'stocks':('85%','980px'),'detail':('75%','980px')}.get(stage,('100%','0px'))
    st.markdown('<style>:root{--reading-width:'+percent+';--reading-min:'+minimum+'}'+READABILITY_CSS+'</style>',unsafe_allow_html=True)
