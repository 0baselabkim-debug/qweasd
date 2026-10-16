"""Read-only official document comparison. Never substitutes a receipt or financial value."""
import gzip,hashlib,html,io,json,re,sys,zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from screener.corporate import load_state

def text(xml):return re.sub(r'\s+',' ',html.unescape(re.sub(r'<[^>]+>',' ',xml))).strip()
def extract(raw,filing):
    if not zipfile.is_zipfile(io.BytesIO(raw)):
        status=re.search(r'<status>(\d+)</status>',raw.decode('utf-8',errors='replace'))
        return {'status':'DART_'+status.group(1) if status else 'NOT_ZIP'}
    documents=[]
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        files=[f for f in z.infolist() if f.filename.lower().endswith('.xml')]
        if sum(f.file_size for f in files)>60000000:raise ValueError('document_expansion_limit')
        for f in files:
            xml=z.read(f).decode('utf-8');company=re.search(r'<COMPANY-NAME\b[^>]*AREGCIK="([^"]+)"',xml)
            title=re.search(r'<DOCUMENT-NAME\b[^>]*>(.*?)</DOCUMENT-NAME>',xml,re.S)
            correction=re.search(r'<CORRECTION>(.*?)</CORRECTION>',xml,re.S)
            sections={}
            for section in re.findall(r'<SECTION-2\b[^>]*>(.*?)</SECTION-2>',xml,re.S):
                st=re.search(r'<TITLE\b[^>]*>(.*?)</TITLE>',section,re.S)
                if not st:continue
                label=text(st.group(1))
                if label in ['2. 연결재무제표','4. 재무제표']:
                    if label in sections:raise ValueError('ambiguous_financial_section')
                    body=text(section);sections[label]={'text':body,'sha256':hashlib.sha256(body.encode()).hexdigest(),
                        'numbers':re.findall(r'(?<![\w])\(?-?\d[\d,.]*\)?',body),'units':sorted(set(re.findall(r'단위\s*:\s*[^)]+',body)))}
            title=text(title.group(1)) if title else None
            plain=text(xml)
            opinions=[plain[max(0,m.start()-80):m.start()+300] for m in re.finditer('감사의견|검토의견',plain)][:12]
            identity=bool(company and company.group(1)==filing['corp_code'] and f.filename.startswith(filing['receipt']))
            documents.append({'filename':f.filename,'document_title':title,'corporate_code':company.group(1) if company else None,
                              'identity_verified':identity,'correction_text':text(correction.group(1)) if correction else None,
                              'financial_sections':sections,'opinion_excerpts':opinions,'xml_sha256':hashlib.sha256(xml.encode()).hexdigest()})
    return {'status':'DOCUMENTS_EXTRACTED','raw_sha256':hashlib.sha256(raw).hexdigest(),'documents':documents,'final_financial_acceptance':False}

def run(output=None):
    state=load_state();root=Path('reports/operations-stabilization/dart-source');result={'corrections':[],'attachments':[]}
    for receipt,parent in [('20260605000456','20260318000902'),('20260605000334','20260312001121')]:
        row={'receipt':receipt,'parent_receipt':parent,'filing':state['filings'][receipt]}
        for key,r in [('corrected',receipt),('original',parent)]:
            path=root/(r+'.document.gz')
            row[key]=extract(gzip.decompress(path.read_bytes()),state['filings'][r]) if path.exists() else {'status':'NOT_RECEIVED'}
        def primary(part):
            docs=[d for d in row[part].get('documents',[]) if d['identity_verified'] and d['document_title']=='사업보고서']
            return docs[0]['financial_sections'] if len(docs)==1 else {}
        a,b=primary('corrected'),primary('original');row['comparisons']={}
        for scope in set(a)|set(b):
            row['comparisons'][scope]={'both_present':scope in a and scope in b,'text_equal':scope in a and scope in b and a[scope]['text']==b[scope]['text'],
                                       'number_sequence_equal':scope in a and scope in b and a[scope]['numbers']==b[scope]['numbers'],
                                       'units_equal':scope in a and scope in b and a[scope]['units']==b[scope]['units']}
        row['status']='REVIEWED_SOURCE_COMPARISON_API_RECEIPT_STILL_HELD';result['corrections'].append(row)
    for receipt,job in state['jobs'].items():
        if job.get('financial_status')!='held_attachment_only':continue
        path=root/(receipt+'.document.gz');row={'receipt':receipt,'filing':state['filings'][receipt],'parent_receipt':job['linked_periodic_receipt']}
        row['evidence']=extract(gzip.decompress(path.read_bytes()),state['filings'][receipt]) if path.exists() else {'status':'NOT_RECEIVED'}
        row['status']='REVIEW_NEEDED_NO_INFERRED_FINANCIAL_OR_RISK_CLEARANCE';result['attachments'].append(row)
    out=Path(output or 'reports/operations-stabilization/correction-review.json');out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'corrections':[(r['receipt'],r['comparisons']) for r in result['corrections']],
                      'attachments':[(r['receipt'],r['evidence']['status']) for r in result['attachments']]},ensure_ascii=False))
if __name__=='__main__':run(sys.argv[1] if len(sys.argv)>1 else None)
