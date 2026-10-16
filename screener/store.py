"""Date-keyed DuckDB storage. Imports are transactional and never replace other dates."""
from pathlib import Path
import json
import duckdb
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
LOCAL_DB=ROOT/'data/dashboard.duckdb'
PACKAGED_DB=ROOT/'snapshot/dashboard.duckdb'
DB=LOCAL_DB if LOCAL_DB.exists() else PACKAGED_DB
SCHEMA='''
CREATE TABLE IF NOT EXISTS instruments(symbol VARCHAR PRIMARY KEY,name VARCHAR,market VARCHAR,listing_date DATE,special BOOLEAN);
CREATE TABLE IF NOT EXISTS memberships(symbol VARCHAR,market VARCHAR,sector VARCHAR,valid_from DATE,valid_to DATE,source VARCHAR,PRIMARY KEY(symbol,valid_from));
CREATE TABLE IF NOT EXISTS flows(symbol VARCHAR,trade_date DATE,net_amount BIGINT,net_quantity BIGINT,exchange VARCHAR,source VARCHAR,finality VARCHAR,PRIMARY KEY(symbol,trade_date));
CREATE TABLE IF NOT EXISTS prices(symbol VARCHAR,trade_date DATE,close DOUBLE,adjusted_close DOUBLE,turnover DOUBLE,volume BIGINT,market_cap DOUBLE,source VARCHAR,exchange VARCHAR,market_cap_source VARCHAR,market_cap_quality VARCHAR,market_cap_observed_at VARCHAR,PRIMARY KEY(symbol,trade_date));
CREATE TABLE IF NOT EXISTS financials(symbol VARCHAR,period_end DATE,disclosed_on DATE,disclosure_verified BOOLEAN,scope VARCHAR,currency VARCHAR,revenue DOUBLE,operating_profit DOUBLE,net_income DOUBLE,equity DOUBLE,liabilities DOUBLE,ocf DOUBLE,previous_revenue DOUBLE,previous_operating_profit DOUBLE,previous_equity DOUBLE,receipt VARCHAR,source VARCHAR,parent_net_income DOUBLE,parent_equity DOUBLE,PRIMARY KEY(symbol,period_end,receipt));
CREATE TABLE IF NOT EXISTS benchmarks(market VARCHAR,trade_date DATE,close DOUBLE,PRIMARY KEY(market,trade_date));
CREATE TABLE IF NOT EXISTS calendar(market VARCHAR,trade_date DATE,PRIMARY KEY(market,trade_date));
CREATE TABLE IF NOT EXISTS exclusions(symbol VARCHAR,name VARCHAR,trade_date DATE,reason VARCHAR,PRIMARY KEY(symbol,trade_date));
CREATE TABLE IF NOT EXISTS metadata(key VARCHAR PRIMARY KEY,value VARCHAR);
'''

def connect(path=DB,read_only=False):
    if not read_only:Path(path).parent.mkdir(parents=True,exist_ok=True)
    c=duckdb.connect(str(path),read_only=read_only)
    if not read_only:c.execute(SCHEMA)
    return c

def load(path=DB):
    from .publication import selected
    with connect(selected(path),read_only=True) as c:
        tables={name:c.execute(f'SELECT * FROM {name}').df() for name in ['instruments','memberships','flows','prices','financials','benchmarks','calendar','exclusions']}
        tables['metadata']={k:json.loads(v) for k,v in c.execute('SELECT key,value FROM metadata').fetchall()}
    return tables

def insert_frame(c,table,frame,preserve_existing=False):
    if frame.empty:return
    cols=c.execute(f'DESCRIBE {table}').df()['column_name'].tolist()
    optional={'prices':{'market_cap_source','market_cap_quality','market_cap_observed_at'},'financials':{'parent_net_income','parent_equity'}}.get(table,set())
    if set(frame.columns)-set(cols) or set(cols)-set(frame.columns)-optional:raise ValueError(f'{table}: column mismatch')
    incoming=[col for col in cols if col in frame.columns]
    c.register('_incoming',frame[incoming])
    action='IGNORE' if preserve_existing else 'REPLACE'
    c.execute(f'INSERT OR {action} INTO main.{table} ({",".join(incoming)}) SELECT * FROM _incoming');c.unregister('_incoming')
