"""Identify original periodic business documents and attachment-only corrections."""
import io,re,zipfile
from screener.corporate import business_section,filing_record,financial_record

def document_section(raw,filing):
    if not zipfile.is_zipfile(io.BytesIO(raw)):
        text=raw.decode('utf-8',errors='replace')
        status=re.search(r'<status>\s*(\d{3})\s*</status>',text)
        if status:raise ValueError('DART_document_status_'+status.group(1))
        raise zipfile.BadZipFile('Document response is not ZIP')
    candidates=[]
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        files=[i for i in z.infolist() if i.filename.lower().endswith('.xml') and i.file_size<30000000]
        if not files:raise ValueError('document_xml_missing')
        if sum(i.file_size for i in files)>60000000:raise ValueError('document_expansion_limit')
        for item in files:
            xml=z.read(item).decode('utf-8')
            company=re.search(r'<COMPANY-NAME\b[^>]*AREGCIK="([^"]+)"',xml)
            title=re.search(r'<DOCUMENT-NAME\b[^>]*>(.*?)</DOCUMENT-NAME>',xml,re.S)
            expected={'annual':'사업보고서','half':'반기보고서','quarter':'분기보고서'}[filing['form']]
            if not company or company.group(1)!=filing['corp_code'] or not title or title.group(1).strip()!=expected:continue
            if not item.filename.startswith(filing['receipt']):continue
            try:candidates.append((item.filename,business_section(xml)))
            except ValueError:continue
    if len(candidates)!=1:raise ValueError('business_document_not_unique')
    return candidates[0]

def attachment_parent(api,filing):
    if not filing['name'].startswith('[첨부정정]'):raise ValueError('not_attachment_correction')
    listing=api.get('list.json',{'corp_code':filing['corp_code'],'bgn_de':str(filing['year']+1)+'0101',
        'end_de':filing['disclosed_on'].replace('-',''),'pblntf_ty':'A','last_reprt_at':'N','page_count':100})
    if listing.get('total_page',0)>1:raise ValueError('attachment_listing_requires_pagination')
    official={r['rcept_no']:r for r in listing.get('list',[]) if r.get('corp_code')==filing['corp_code']}
    if filing['receipt'] not in official:raise ValueError('attachment_not_in_official_list')
    for scope in ['CFS','OFS']:
        response=api.get('fnlttSinglAcntAll.json',{'corp_code':filing['corp_code'],'bsns_year':str(filing['year']),'reprt_code':filing['report_code'],'fs_div':scope})
        if response.get('status')=='013':continue
        receipts={r.get('rcept_no') for r in response.get('list',[])}
        if len(receipts)!=1:raise ValueError('receipt_or_period_mismatch')
        receipt=receipts.pop();row=official.get(receipt)
        parent=filing_record(row,filing['symbol']) if row else None
        if not parent or parent['name'].startswith('[첨부정정]') or any(parent[k]!=filing[k] for k in ['corp_code','year','report_code','period_end','form']):raise ValueError('attachment_parent_not_verified')
        if parent['disclosed_on']>filing['disclosed_on']:raise ValueError('attachment_parent_after_disclosure')
        if any(r.get('corp_code')!=filing['corp_code'] for r in response['list']):raise ValueError('corporate_identity_mismatch')
        record=financial_record(response,parent,scope)
        return parent,record,{'parent_receipt':receipt,'scope':scope,'financial_response_sha256':record['response_sha256'],
            'relation':'same issuer/period/form in official disclosure list and exact financial response; attachment content not assumed unchanged'}
    raise ValueError('attachment_parent_financial_unavailable')
