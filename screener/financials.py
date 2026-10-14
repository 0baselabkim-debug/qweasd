"""Conservative OpenDART account matching within one report and statement scope."""
import re
import unicodedata
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path

ACCOUNTS = {
    'revenue': (('IS','CIS'), ('ifrs-full_Revenue','ifrs_Revenue'), ('매출액','매출','영업수익','수익매출액')),
    'operating_profit': (('IS','CIS'), ('dart_OperatingIncomeLoss','ifrs-full_ProfitLossFromOperatingActivities'), ('영업이익','영업이익손실','영업손실')),
    'net_income': (('IS','CIS'), ('ifrs-full_ProfitLoss','ifrs_ProfitLoss'), ('당기순이익','당기순이익손실','당기순손실','당기연결순이익손실','연결당기순이익손실','당기순이익의귀속','당기순이익손실의귀속')),
    'equity': (('BS',), ('ifrs-full_Equity','ifrs_Equity'), ('자본총계',)),
    'liabilities': (('BS',), ('ifrs-full_Liabilities','ifrs_Liabilities'), ('부채총계',)),
    'ocf': (('CF',), ('ifrs-full_CashFlowsFromUsedInOperatingActivities','ifrs_CashFlowsFromUsedInOperatingActivities'),
            ('영업활동현금흐름','영업활동순현금흐름','영업활동으로인한현금흐름','영업활동으로인한순현금흐름','영업활동으로부터의현금흐름','영업활동으로부터의순현금흐름')),
    'parent_net_income': (('IS','CIS'), ('ifrs-full_ProfitLossAttributableToOwnersOfParent',), ('지배기업의소유주에게귀속되는당기순이익손실','지배기업의소유주에게귀속되는당기순이익','지배주주귀속당기순이익','지배기업소유주귀속당기순이익손실','지배회사지분순손익','지배기업의소유주귀속당기순이익','지배기업의소유주귀속순손익','지배기업소유주지분순이익손실','지배기업당기순이익','지배지분순이익','지배주주지분순이익','지배기업소유주지분이익손실')),
    'parent_equity': (('BS',), ('ifrs-full_EquityAttributableToOwnersOfParent',), ('지배기업의소유주에게귀속되는자본','지배기업소유주지분','지배지분','지배기업소유주귀속분','지배기업주주지분')),
    '_noncontrolling_net_income': (('IS','CIS'), ('ifrs-full_ProfitLossAttributableToNoncontrollingInterests',), ('비지배지분에귀속되는당기순이익손실','비지배지분귀속당기순이익')),
    '_noncontrolling_equity': (('BS',), ('ifrs-full_NoncontrollingInterests',), ('비지배지분',)),
}

def number(value):
    if value is None or str(value).strip() in ('','-'):return None
    text=str(value).strip().replace(',','')
    if text.startswith('(') and text.endswith(')'):text='-'+text[1:-1]
    try:
        n=Decimal(text)
        return int(n) if n.is_finite() and n==n.to_integral_value() else None
    except InvalidOperation:return None

def normalized_name(name):
    text=unicodedata.normalize('NFKC',name)
    text=re.sub(r'^\s*[IVXⅠⅡⅢⅣⅤ0-9]+[.．)\s]+','',text)
    return re.sub(r'[\s()·ㆍ.,]','',text)

def select_account(rows, field):
    statements,ids,names=ACCOUNTS[field]
    pool=[];method='missing'
    for use_id in [True,False]:
        for statement in statements:
            candidates=[r for r in rows if r.get('sj_div')==statement and r.get('account_detail','-') in ('-','')]
            if use_id:
                pool=[r for r in candidates if r.get('account_id') in ids]
                method='standard_account_id'
            else:
                # A different standard code (e.g. comprehensive income) is not an alias.
                pool=[r for r in candidates if not r.get('account_id','').startswith(('ifrs','dart_'))
                      and normalized_name(r.get('account_nm','')) in names]
                method='exact_normalized_name'
            if pool:break
        if pool:break
    def one(column):
        values={number(r.get(column)) for r in pool};values.discard(None)
        return next(iter(values)) if len(values)==1 else None
    result={'value':one('thstrm_amount'),'previous':one('frmtrm_amount'),'method':method if pool else 'missing',
            'account_ids':sorted({r.get('account_id','') for r in pool}),
            'account_names':sorted({r.get('account_nm','') for r in pool}),
            'ambiguous':len({number(r.get('thstrm_amount')) for r in pool}-{None})>1}
    return result

def parse_full_response(payload, receipt, scope):
    if payload.get('scope')!=scope or payload.get('requested_receipt')!=receipt:
        raise ValueError('report_scope_mismatch')
    response=payload.get('response',{});rows=response.get('list',[])
    if response.get('status')!='000' or not rows:raise ValueError('no_full_statement')
    if any(r.get('rcept_no')!=receipt or r.get('bsns_year')!='2025' or r.get('reprt_code')!='11011' for r in rows):
        raise ValueError('report_identity_mismatch')
    if {r.get('currency') for r in rows}!={'KRW'}:raise ValueError('currency_not_verified_KRW')
    result={field:select_account(rows,field) for field in ACCOUNTS}
    parent,nci=result['parent_net_income'],result['_noncontrolling_net_income']
    if scope=='CFS' and result['net_income']['value'] is None and not result['net_income']['ambiguous'] and parent['value'] is not None and nci['value'] is not None:
        result['net_income']={'value':parent['value']+nci['value'],'previous':None,
                              'method':'explicit_parent_plus_explicit_noncontrolling','ambiguous':False,
                              'account_ids':parent['account_ids']+nci['account_ids'],'account_names':parent['account_names']+nci['account_names'],
                              'components':{'parent':parent['value'],'noncontrolling':nci['value']}}
    # Explicit accounting identity, never a zero assumption for a missing interest.
    for parent,total,noncontrolling in [('parent_net_income','net_income','_noncontrolling_net_income'),('parent_equity','equity','_noncontrolling_equity')]:
        a,b=result[total],result[noncontrolling]
        if scope=='CFS' and result[parent]['value'] is None and not result[parent]['ambiguous'] and a['value'] is not None and b['value'] is not None:
            result[parent]={'value':a['value']-b['value'],'previous':None,
                            'method':'explicit_total_minus_explicit_noncontrolling','ambiguous':False,
                            'account_ids':a['account_ids']+b['account_ids'],'account_names':a['account_names']+b['account_names'],
                            'components':{'total':a['value'],'noncontrolling':b['value']}}
    result.pop('_noncontrolling_net_income');result.pop('_noncontrolling_equity')
    overrides=Path(__file__).resolve().parents[1]/'account_overrides.json'
    if overrides.exists():
        for fix in json.loads(overrides.read_text())['overrides']:
            if (payload.get('symbol'),scope,receipt)!=(fix['symbol'],fix['scope'],fix['receipt']):continue
            if fix.get('kind')=='filing_text':
                if not fix.get('document_sha256') or not fix.get('evidence'):raise ValueError('unverified_document_override')
                result[fix['field']]={'value':fix['expected_value'],'previous':fix.get('previous_value'),
                                      'method':'reviewed_original_document_value','account_ids':[fix['account_id']],
                                      'account_names':[fix['account_name']],'ambiguous':False,'evidence_url':fix['source_url']}
                continue
            matching=[r for r in rows if r.get('sj_div')=='CF' and r.get('account_id')==fix['account_id']
                      and r.get('account_nm')==fix['account_name'] and r.get('account_detail','-')=='-']
            values={number(r.get('thstrm_amount')) for r in matching}
            if values!={fix['expected_value']}:raise ValueError('reviewed_account_override_changed')
            result[fix['field']]={'value':fix['expected_value'],'previous':None,'method':'reviewed_original_filing',
                                  'account_ids':[fix['account_id']],'account_names':[fix['account_name']],
                                  'ambiguous':False,'evidence_url':fix['source_url']}
    return result
