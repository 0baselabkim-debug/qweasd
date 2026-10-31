"""Offline event/financial evidence contracts. No API clients or title sentiment."""
import datetime as dt
from fractions import Fraction
from .metrics import metric
from screener.decision_support.evidence import timestamp

TYPES={'periodic_filing','preliminary_earnings','supply_contract','contract_change','contract_cancellation',
       'contract_delay','buyback','share_cancellation','equity_issuance','convertible_bond','business_reorganization','scheduled_event'}


def event_evidence(value, stock_code, cutoff):
    if value.get('event_type') not in TYPES:raise ValueError('unsupported_event_type')
    dt.date.fromisoformat(value['event_date'])
    source=value.get('source',{})
    if not all(source.get(k) for k in ('id','url','sha256')):raise ValueError('event_original_reference_required')
    if len(source['sha256'])!=64 or any(c not in '0123456789abcdef' for c in source['sha256']):raise ValueError('invalid_event_source_hash')
    relation=value.get('relationship')
    if relation not in ('direct','industry_peer','unknown'):raise ValueError('event_relationship_required')
    if relation=='direct' and stock_code not in value.get('related_stock_codes',[]):raise ValueError('direct_company_relationship_not_proven')
    public=value.get('published_at')
    eligible=False;reason='exact_public_time_unknown'
    if public:
        eligible=timestamp(public)<=timestamp(cutoff)
        reason=None if eligible else 'published_after_knowledge_cutoff'
    facts=value.get('verified_facts',[])
    if not isinstance(facts,list) or not all(isinstance(x,str) for x in facts):raise ValueError('facts_must_be_explicit_strings')
    return dict(stock_code=stock_code,event_type=value['event_type'],event_date=value['event_date'],published_at=public,
        source=source,relationship=relation,directly_related=relation=='direct',verified_facts=facts if eligible and value.get('validation_status')=='verified' else [],
        validation_status=value.get('validation_status','UNVERIFIED'),available_at_cutoff=eligible,
        unknowns=value.get('unknowns',[]),availability_reason=reason,price_impact=None)


def consensus_surprise(actual, consensus, announcement_at):
    if actual.get('validation_status')!='verified' or not actual.get('source_id'):
        return metric(None,'actual_financial_report_not_verified')
    if (consensus.get('validation_status')!='verified' or not consensus.get('source_id')
            or not consensus.get('published_at') or timestamp(consensus['published_at'])>=timestamp(announcement_at)):
        return metric(None,'pre_announcement_verified_consensus_missing')
    for key in ('period','accounting_scope','currency','metric'):
        if actual.get(key)!=consensus.get(key):return metric(None,'actual_consensus_basis_mismatch')
    expected=consensus.get('value');observed=actual.get('value')
    if expected is None or observed is None or expected<=0:return metric(None,'nonpositive_or_missing_consensus_denominator')
    return metric(float((Fraction(observed)-Fraction(expected))/Fraction(expected)*100))
