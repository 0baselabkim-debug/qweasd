"""Supabase Auth + PostgREST. Only server-verified user JWTs access private records."""
import datetime as dt,json,os,re,urllib.request,urllib.error,urllib.parse
from decimal import Decimal,ROUND_HALF_UP,InvalidOperation
from zoneinfo import ZoneInfo

class StoreError(Exception):pass
class ConflictError(StoreError):pass

def http(url,key,path,method='GET',body=None,token=None,prefer=None):
    if not re.fullmatch(r'https://[a-z0-9-]+\.supabase\.co',url):raise StoreError('Supabase URL 형식 오류')
    headers={'apikey':key,'Content-Type':'application/json'}
    if token:headers['Authorization']='Bearer '+token
    if prefer:headers['Prefer']=prefer
    try:
        req=urllib.request.Request(url+path,data=json.dumps(body).encode() if body is not None else None,headers=headers,method=method)
        with urllib.request.urlopen(req,timeout=20) as res:
            raw=res.read();return json.loads(raw) if raw else None
    except (urllib.error.URLError,ValueError,TimeoutError):
        # Never expose upstream body, JWT, password, SQL errors or personal data.
        raise StoreError('인증 또는 비공개 저장 요청 실패. 설정·권한과 연결 상태를 확인하세요.') from None

class JournalStore:
    def __init__(self,url,key,session):
        self.url=url;self.key=key;self.session=session
        user=self.call('/auth/v1/user')
        if not user or not user.get('id'):raise StoreError('로그인 확인 실패')
        self.user_id=user['id']
    def call(self,path,method='GET',body=None,prefer=None):return http(self.url,self.key,path,method,body,self.session['access_token'],prefer)
    @staticmethod
    def login(url,key,email,password):
        session=http(url,key,'/auth/v1/token?grant_type=password','POST',{'email':email,'password':password})
        if not session or not session.get('access_token'):raise StoreError('로그인 실패')
        return {k:session[k] for k in ['access_token','refresh_token','expires_at'] if k in session}
    @staticmethod
    def refresh(url,key,session):
        if session.get('expires_at',0)>dt.datetime.now(dt.timezone.utc).timestamp()+60:return session
        result=http(url,key,'/auth/v1/token?grant_type=refresh_token','POST',{'refresh_token':session['refresh_token']})
        return {k:result[k] for k in ['access_token','refresh_token','expires_at'] if k in result}
    def logout(self):self.call('/auth/v1/logout','POST',{})
    def purchases(self):return self.call('/rest/v1/journal_purchases?select=*&order=buy_date.asc,created_at.asc') or []
    def save(self,symbol,date,price,quantity,record=None):
        body=validate_purchase(symbol,date,price,quantity)
        if record:
            path='/rest/v1/journal_purchases?'+urllib.parse.urlencode({'id':'eq.'+record['id'],'revision':'eq.'+str(record['revision'])})
            result=self.call(path,'PATCH',body,'return=representation')
            if not result:raise ConflictError('다른 창에서 변경된 기록입니다. 새로 조회해 주세요.')
        else:result=self.call('/rest/v1/journal_purchases','POST',body,'return=representation')
        if not result:raise StoreError('저장 결과 확인 실패')
        return result[0]
    def delete(self,record):
        path='/rest/v1/journal_purchases?'+urllib.parse.urlencode({'id':'eq.'+record['id'],'revision':'eq.'+str(record['revision'])})
        if not self.call(path,'DELETE',prefer='return=representation'):raise ConflictError('기록이 변경되었거나 삭제되었습니다. 새로 조회해 주세요.')
    def request(self,symbol):self.call('/rest/v1/rpc/request_technical','POST',{'p_symbol':symbol})
    def cache(self,symbol):
        result=self.call('/rest/v1/technical_cache?'+urllib.parse.urlencode({'symbol':'eq.'+symbol,'select':'bundle,last_error,attempted_at'}))
        return result[0] if result else {}
    def risk(self,symbol):
        result=self.call('/rest/v1/journal_risk_state?'+urllib.parse.urlencode({'symbol':'eq.'+symbol,'select':'*'}));return result[0] if result else {}
    def save_risk(self,symbol,result):
        if 'trailing_stop' in result:self.call('/rest/v1/rpc/save_risk_state','POST',{'p_symbol':symbol,'p_trailing':result['trailing_stop'],'p_activated':result['activated'],'p_asof':result['asof']})

class ServiceStore:
    """Actions only. This class is never instantiated by the public Streamlit UI."""
    def __init__(self,url,key):self.url=url;self.key=key
    @classmethod
    def from_env(cls):
        url=os.getenv('SUPABASE_URL');key=os.getenv('SUPABASE_SERVICE_ROLE_KEY')
        return cls(url,key) if url and key else None
    def call(self,name,body):return http(self.url,self.key,'/rest/v1/rpc/'+name,'POST',body,self.key)
    def targets(self,asof):return self.call('technical_targets',{'p_asof':asof}) or []
    def cache(self,symbol):
        rows=http(self.url,self.key,'/rest/v1/technical_cache?'+urllib.parse.urlencode({'symbol':'eq.'+symbol,'select':'bundle'}),token=self.key) or []
        return rows[0].get('bundle') if rows else None
    def publish(self,symbol,bundle):self.call('publish_technical',{'p_symbol':symbol,'p_bundle':bundle,'p_error':None})
    def positions(self,symbol):return self.call('worker_positions',{'p_symbol':symbol}) or []
    def save_signal(self,position,result):self.call('save_worker_signal',{'p_user':position['user_id'],'p_symbol':position['symbol'],'p_signature':position['purchase_signature'],'p_result':result})
    def fail(self,symbol,message):self.call('publish_technical',{'p_symbol':symbol,'p_bundle':None,'p_error':message})

def validate_purchase(symbol,date,price,quantity):
    try:
        if not re.fullmatch(r'\d{6}',str(symbol)):raise ValueError()
        day=dt.date.fromisoformat(str(date));today=dt.datetime.now(ZoneInfo('Asia/Seoul')).date()
        p=Decimal(str(price));q=Decimal(str(quantity))
        if not p.is_finite() or not q.is_finite() or p<=0 or p>10**9 or q<=0 or q>10**9 or q!=q.to_integral_value() or day>today:raise ValueError()
        if p!=p.quantize(Decimal('.0001')):raise ValueError()
    except (ValueError,InvalidOperation):raise StoreError('종목·매수일·양수 가격(소수 4자리 이하)·정수 수량을 확인하세요.') from None
    return {'symbol':symbol,'buy_date':str(day),'average_price':str(p),'quantity':int(q)}

def aggregate(records):
    totals={}
    for r in records:
        symbol=r['symbol'];p=validate_purchase(symbol,r['buy_date'],r['average_price'],r['quantity']);q=p['quantity']
        row=totals.setdefault(symbol,{'symbol':symbol,'quantity':0,'cost':Decimal(0),'buy_date':p['buy_date'],'records':[]})
        row['quantity']+=q;row['cost']+=Decimal(p['average_price'])*q;row['buy_date']=min(row['buy_date'],p['buy_date']);row['records'].append(r)
    for row in totals.values():row['average_price']=row['cost']/row['quantity']
    return list(totals.values())

def valuation(position,close):
    if close is None:return {'pnl':None,'return':None}
    close=Decimal(str(close));pnl=close*position['quantity']-position['cost']
    return {'pnl':pnl,'return':pnl/position['cost']*100}

def display_decimal(value,places=2):
    if value is None:return '미확보'
    return f"{Decimal(str(value)).quantize(Decimal(1).scaleb(-places),rounding=ROUND_HALF_UP):,.{places}f}"
