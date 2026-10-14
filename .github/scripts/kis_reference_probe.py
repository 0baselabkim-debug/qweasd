"""Inspect official public reference files without API credentials."""
import base64,gzip,hashlib,io,json,re,urllib.request,zipfile
from pathlib import Path
expected={'KOSPI':'abdeb3d936f26e793352fda289591ee45e33309d7faf05c72db4a6a635322e0f','KOSDAQ':'f990e4b138e4afa10701bf9e45314f9ed138db76d779f1c57959c60a519794db'}
records=[];sources=[]
for market,l in json.loads(Path('.github/scripts/kis-master-layouts.json').read_text()).items():
    with urllib.request.urlopen(l['url'],timeout=30) as r:raw=r.read()
    sha=hashlib.sha256(raw).hexdigest()
    sources.append({'market':market,'url':l['url'],'sha256':sha,'matches_original_20261014':sha==expected[market]})
    with zipfile.ZipFile(io.BytesIO(raw)) as z:lines=z.read(l['member']).decode('cp949').splitlines()
    width=sum(l['widths'])
    for line in lines:
        code=line[:9].strip()
        if not re.fullmatch(r'\d{6}',code):continue
        tail=line[-width:];offset=0;fields=[]
        for n in l['widths']:fields.append(tail[offset:offset+n].strip());offset+=n
        if fields[0]!='ST':continue
        records.append({'symbol':code,'market':market,'name':line[21:-width].strip(),'preferred':fields[l['preferred_index']],'listed_shares_raw':fields[50 if market=='KOSPI' else 45],'previous_cap_raw':fields[65 if market=='KOSPI' else 59],'industry':[fields[2],fields[3],fields[4]]})
result={'sources':sources,'records':records}
try:
    url='https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13'
    with urllib.request.urlopen(url,timeout=40) as r:raw=r.read()
    Path('kind-company-list.xls').write_bytes(raw)
    from html.parser import HTMLParser
    class Table(HTMLParser):
        def __init__(self): super().__init__(); self.rows=[]; self.row=[]; self.text=[]; self.cell=False
        def handle_starttag(self,tag,attrs):
            if tag=='tr':self.row=[]
            if tag in ('td','th'):self.cell=True;self.text=[]
        def handle_data(self,data):
            if self.cell:self.text.append(data)
        def handle_endtag(self,tag):
            if tag in ('td','th'):self.row.append(''.join(self.text).strip());self.cell=False
            if tag=='tr' and self.row:self.rows.append(self.row)
    for encoding in ['utf-8','euc-kr','cp949']:
        try:html=raw.decode(encoding);break
        except UnicodeDecodeError:pass
    table=Table();table.feed(html)
    result['kind']={'url':url,'sha256':hashlib.sha256(raw).hexdigest(),'header':table.rows[:1],'records':[]}
    for row in table.rows[1:]:
        if len(row)>5 and re.fullmatch(r'\d{6}',row[2]):
            result['kind']['records'].append({'symbol':row[2],'industry':row[3],'listing_date':row[5]})
except Exception as e:print('::notice title=KRX listing download::'+json.dumps({'error':type(e).__name__}))

packed=base64.b64encode(gzip.compress(json.dumps(result,ensure_ascii=False,separators=(',',':')).encode(),mtime=0)).decode()
Path('reference-data.b64').write_text(packed)
print('::notice title=Reference summary::'+json.dumps({'sources':sources,'master_rows':len(records),'kind_rows':len(result.get('kind',{}).get('records',[])),'chunks':(len(packed)+2799)//2800,'samples':[r for r in records if r['symbol'] in ['005930','058470','001000']]}))
