"""Presentation only: dark panels, badges and formatted copies of actual values."""
import html
import pandas as pd
import streamlit as st

CSS='''<style>
:root{color-scheme:dark;--bg:#070f1b;--panel:#0e192b;--line:#203653;--blue:#2167ff;--red:#ff5279;--text:#eef4ff}
.stApp{background:radial-gradient(ellipse at 80% 0%,#11234055,transparent 45%),#070f1b;color:var(--text)}
[data-testid="stHeader"]{background:transparent}
.block-container{max-width:1720px;padding:2.3rem 1.3rem 3rem}
[data-testid="stSidebar"]{background:#0a1424;border-right:1px solid #203653}
h1{font-size:2.5rem!important;font-weight:800!important;letter-spacing:-.065em;line-height:1.25!important;margin:0!important;padding:0!important}
h2{font-size:1.2rem!important;font-weight:750!important;padding:0!important;letter-spacing:-.035em}
h3{font-size:1.05rem!important;letter-spacing:-.025em}
[data-testid="stVerticalBlock"]{gap:.7rem}
[data-testid="stSelectbox"]>label,[data-testid="stRadio"]>label{font-size:.76rem;color:#a6bcda}
[data-baseweb="select"]>div{background:#091424!important;border-color:#365a89!important;border-radius:7px!important}
[data-testid="stTextInput"] input{background:#091424;color:#edf4ff}
[data-testid="stTabs"] [role="tablist"]{gap:3px;border-bottom:1px solid #2457a0;padding-bottom:0;background:transparent}
[data-testid="stTabs"] [role="tab"]{height:43px;padding:0 24px;border-radius:8px 8px 0 0;background:linear-gradient(115deg,#1a304e,#13243d);color:#d9e7ff;border:1px solid #223c5e;border-bottom:0;font-weight:650}
[data-testid="stTabs"] [role="tab"][aria-selected="true"]{background:linear-gradient(125deg,#287bff,#1856ed);color:white;box-shadow:0 -2px 20px #1e64ff20 inset;border-color:#4889ff}
[data-testid="stTabs"] [data-baseweb="tab-highlight"]{background:#4889ff;height:2px}
.st-key-workspace_view>div>div[role="tablist"] [role="tab"]{min-width:210px;height:49px;font-size:1rem}
.st-key-sector_period [role="tab"],.st-key-stock_period [role="tab"]{min-width:140px;height:38px;border-radius:7px}
.st-key-sector_period [role="tablist"],.st-key-stock_period [role="tablist"]{border:0}
[data-testid="stVerticalBlockBorderWrapper"]>div{border-radius:11px}
.st-key-sector_panel,.st-key-sector_context,.st-key-stock_panel,.st-key-detail_panel,.st-key-financial_panel,.st-key-score_panel{background:linear-gradient(135deg,#101d30,#0b1626);border:1px solid #203653!important;border-radius:11px;padding:17px;box-shadow:0 8px 25px #00000022}
[data-testid="stDataFrame"]{border:1px solid #203653;border-radius:8px;overflow:hidden}
[data-testid="stButton"] button,[data-testid="stDownloadButton"] button,[data-testid="stLinkButton"] a{border:1px solid #335c91;background:#0c1b30;color:#dbeaff;border-radius:7px;min-height:36px;box-shadow:inset 0 0 12px #2365ff08}
[data-testid="stButton"] button:hover,[data-testid="stDownloadButton"] button:hover,[data-testid="stLinkButton"] a:hover{border-color:#4a8aff;background:#142c50;color:white}
[data-testid="stRadio"] [role="radiogroup"]{gap:12px}
[data-testid="stExpander"]{background:#0c1729;border-color:#233c5d;border-radius:8px}
[data-testid="stVegaLiteChart"]{background:#091422;border:1px solid #1c304a;border-radius:8px;padding:0;box-sizing:border-box}
.date-label{height:42px;display:flex;align-items:center;justify-content:flex-end;font-size:14px;color:#d6e5ff;white-space:nowrap}
.status-line{display:flex;gap:7px;flex-wrap:wrap;margin:2px 0 7px}
.badge{display:inline-block;padding:3px 8px;border-radius:5px;font-size:11px;line-height:1.7;font-weight:650;border:1px solid #284a76;color:#a9c9ff;background:#112a4c;white-space:nowrap}
.badge.up{color:#ffb0c3;border-color:#713248;background:#3d1b2b}.badge.down{color:#a3ceff;border-color:#244e95;background:#102a50}.badge.hold{color:#dabd83;background:#30291d;border-color:#55462c}
.kpi{height:86px;box-sizing:border-box;border:1px solid #2b4f80;border-radius:8px;padding:12px 15px;background:linear-gradient(130deg,#10223b,#0b1423);box-shadow:inset 0 0 20px #235dff08}
.kpi.up{border-color:#81324a}.kpi.down{border-color:#285ca7}
.kpi-label{font-size:12px;color:#d6e4ff;margin-bottom:8px;white-space:nowrap}.kpi-value{font-size:21px;line-height:1.2;font-weight:750;letter-spacing:-.045em;white-space:nowrap;font-variant-numeric:tabular-nums}.up .kpi-value{color:#ff5d8a}.down .kpi-value{color:#4f9bff}
.stock-name{font-size:24px;letter-spacing:-.05em;font-weight:750;margin:8px 0 5px}.stock-code{font-size:12px;color:#8faed6}.stock-price{font-size:27px;font-weight:750;margin:15px 0;color:#f4f7ff;font-variant-numeric:tabular-nums}
.mini-table{width:100%;border-collapse:separate;border-spacing:0;font-size:13px;font-variant-numeric:tabular-nums;white-space:nowrap}.mini-table th{background:#162b48;color:#cbdfff;text-align:left;padding:7px 10px;font-size:11px;font-weight:650}.mini-table td{border-bottom:1px solid #1c2e45;padding:7px 10px}.mini-table tr:nth-child(even) td{background:#10203555}.mini-table .num{text-align:right}.positive{color:#ff5d7d}.negative{color:#5297ff}.mini-scroll{overflow-x:auto;border:1px solid #203653;border-radius:7px}
.company-info{width:100%;border-collapse:collapse;font-size:13px}.company-info td{padding:10px 0;border-bottom:1px solid #1b2e49}.company-info td:first-child{color:#95b2db}.company-info td:last-child{text-align:right;color:#edf5ff;font-variant-numeric:tabular-nums}
.st-key-sector_flow_chart_mode [role="radiogroup"] label,.st-key-stock_flow_chart_mode [role="radiogroup"] label{background:#112139;border:1px solid #243d5e;border-radius:6px;padding:7px 12px;margin-right:0}
.st-key-sector_flow_chart_mode [role="radiogroup"] label:has(input:checked),.st-key-stock_flow_chart_mode [role="radiogroup"] label:has(input:checked){background:linear-gradient(120deg,#2678ff,#1856ed);border-color:#4388ff}
@media(max-width:900px){.block-container{padding:1.1rem .8rem 2rem}h1{font-size:1.9rem!important}.st-key-workspace_view>div>div[role="tablist"] [role="tab"]{min-width:0;padding:0 16px}.kpi-value{font-size:18px}.st-key-sector_panel,.st-key-sector_context,.st-key-stock_panel,.st-key-detail_panel{padding:12px}}
@media(max-width:640px){h1{font-size:1.8rem!important}.st-key-header [data-testid="stHorizontalBlock"]{flex-wrap:wrap!important}.date-label{justify-content:flex-start;height:30px}.st-key-stock_kpis [data-testid="stHorizontalBlock"]{display:grid!important;grid-template-columns:repeat(2,minmax(0,1fr))!important;gap:8px!important}.st-key-stock_kpis [data-testid="stColumn"]{width:100%!important;min-width:0!important}.kpi{height:84px;padding:10px}.kpi-value{font-size:18px}.st-key-sector_period [role="tab"],.st-key-stock_period [role="tab"]{min-width:0;flex:1;padding:0 13px}[data-testid="stTabs"] [role="tab"] p{font-size:13px}.st-key-workspace_view [role="tablist"]{overflow-x:auto} .mini-table{font-size:12px}.mini-table td{padding:8px}.stock-name{font-size:22px}}
</style>'''

def install():st.markdown(CSS,unsafe_allow_html=True)
def badge(text,kind=''):return f'<span class="badge {kind}">{html.escape(str(text))}</span>'
def badges(items):st.markdown('<div class="status-line">'+''.join(badge(*i) if isinstance(i,tuple) else badge(i) for i in items)+'</div>',unsafe_allow_html=True)
def number(v,d=2):return '—' if pd.isna(v) else f'{v:,.{d}f}'
def tone(v):return '' if pd.isna(v) or v==0 else 'up' if v>0 else 'down'
def kpi(label,value,unit='',kind=''):
 st.markdown(f'<div class="kpi {kind}"><div class="kpi-label">{html.escape(label)}</div><div class="kpi-value">{html.escape(value)} <small>{html.escape(unit)}</small></div></div>',unsafe_allow_html=True)
def styled(frame):
 def amount(v):
  if not isinstance(v,(int,float)) or pd.isna(v):return ''
  return 'color: '+('#ff5d7d' if v>0 else '#5499ff' if v<0 else '#b5c7df')
 def state(v):
  s=str(v)
  if '강화' in s or '순매수 전환' in s:return 'color: #ffb6c7; background-color: #3d1b2b; font-weight: bold'
  if '약화' in s or '순매도 전환' in s:return 'color: #a3ceff; background-color: #102b50'
  return 'color: #a8bbd6'
 result=frame.style.format(precision=2,na_rep="—")
 cols=[c for c in frame if any(w in c for w in ['(억원)','강도(%)','증감'])]
 ranks=[c for c in frame if '순위' in c or '일수' in c or c=='종목 수']
 if ranks:result=result.format('{:.0f}',subset=[c for c in ranks if pd.api.types.is_numeric_dtype(frame[c])],na_rep='—')
 if cols:result=result.map(amount,subset=cols)
 if '수급 변화' in frame:result=result.map(state,subset=['수급 변화'])
 return result

def major_stocks(frame,days):
 rows=[]
 for n,(_,r) in enumerate(frame.sort_values([f'net_{days}d','symbol'],ascending=[False,True],na_position='last').head(5).iterrows(),1):
  value=r[f'net_{days}d']/1e8;cls='positive' if pd.notna(value) and value>0 else 'negative' if pd.notna(value) and value<0 else ''
  rows.append(f'<tr><td>{n}</td><td>{html.escape(r["name"])}</td><td class="num {cls}">{number(value)}</td><td class="num">{number(r[f"intensity_{days}d"]*100)}%</td><td>{badge(r.change_state,"up" if r.change_direction=="강화" else "down")}</td></tr>')
 st.markdown('<div class="mini-scroll"><table class="mini-table"><thead><tr><th>순위</th><th>종목명</th><th class="num">순매수(억원)</th><th class="num">시총 대비</th><th>상태</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table></div>',unsafe_allow_html=True)
