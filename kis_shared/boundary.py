"""KIS physical HTTP fence, independent of an injected urllib opener.

The Session and client budget run before final transport. An unreviewed raw
Session/requests/http.client entry is rejected before socket I/O or bytes sent.
"""
import json
import sys
from urllib.parse import urlsplit
from .lease import Blocked
HOST='openapi.koreainvestment.com'
_installed=False

def authorize(url,headers=None,body=None,method=None,*,transport=False):
    parsed=urlsplit(url);keys={str(k).lower() for k in (headers or {})}
    sensitive=bool(keys & {'appkey','appsecret'})
    if body:
        try:sensitive |= bool({'appkey','appsecret'} & json.loads(body).keys())
        except (ValueError,AttributeError,TypeError):pass
    if parsed.hostname!=HOST:
        if sensitive:raise Blocked('KIS_credentials_must_not_follow_redirect')
        return
    if parsed.scheme!='https' or parsed.port!=9443 or parsed.username or parsed.password or parsed.fragment:
        raise Blocked('KIS_origin_rejected')
    method=method or 'GET';auth=parsed.path=='/oauth2/tokenP'
    allowed=(auth and method=='POST') or (method=='GET' and (parsed.path.startswith('/uapi/domestic-stock/v1/quotations/') or parsed.path in {
        '/uapi/domestic-stock/v1/ksdinfo/'+s for s in ('rev-split','merger-split','bonus-issue','cap-dcrs')}))
    if not allowed or '..' in parsed.path:raise Blocked('KIS_order_or_unreviewed_endpoint_rejected')
    from .runtime import current
    session=current();identity=(method,parsed.path,parsed.query)
    with session.reservation_lock:
        if transport:
            # A raw low-level request lacks the reviewed urllib dispatch and budget.
            if getattr(session,'transport_pending',None)!=identity:
                raise Blocked('KIS_unreviewed_low_level_transport_rejected')
            session.transport_pending=None;session.check()
        else:
            if getattr(session,'transport_pending',None) is not None:
                raise Blocked('KIS_second_dispatch_before_response_closed')
            session.authorize_network(auth);session.transport_pending=identity

def install():
    global _installed
    if _installed:return
    _installed=True
    def audit(event,args):
        if event=='urllib.Request':
            url,data,headers,method=args;authorize(url,headers,data,method)
        elif event=='http.client.connect':
            _,host,port=args
            if host==HOST:
                from .runtime import current
                current().check()
        elif event=='http.client.send':
            connection,data=args
            if not isinstance(data,bytes):return
            if data.startswith((b'GET ',b'POST ',b'PUT ',b'DELETE ',b'PATCH ',b'HEAD ')) and b' HTTP/' in data.split(b'\r\n',1)[0]:
                host=getattr(connection,'_tunnel_host',None) or getattr(connection,'host','')
                if host==HOST:
                    line=data.split(b'\r\n',1)[0].decode('ascii');method,path,_=line.split(' ',2)
                    authorize('https://'+HOST+':9443'+path,method=method,transport=True)
    sys.addaudithook(audit)
