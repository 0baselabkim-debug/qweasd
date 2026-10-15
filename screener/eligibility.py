"""Approval-gated financial suitability. Never changes the flow universe or order."""
from pathlib import Path
import json,operator
import pandas as pd
RULES=Path(__file__).resolve().parents[1]/'eligibility_rules.json'
OPS={'lt':operator.lt,'le':operator.le,'gt':operator.gt,'ge':operator.ge,'eq':operator.eq,'in':lambda a,b:a in b}
STATES={'투자 적격','주의','투자 부적격','판정 보류'}
def missing(value):return value is None or (not isinstance(value,(str,list,dict)) and pd.isna(value))
def assess(row,rules=None):
    cfg=rules if rules is not None else json.loads(RULES.read_text())
    required=[k for k in cfg['required_fields'] if missing(row.get(k))]
    facts=[]
    for label,key in [('자본총계','equity'),('자본금','capital_stock'),('부채비율','debt_ratio'),('영업이익','operating_profit'),('전년 영업이익','previous_operating_profit'),('영업현금흐름','ocf'),('매출 성장률','revenue_growth'),('감사의견','audit_opinion'),('중대한 재무 위험','material_financial_risk')]:
        v=row.get(key);text='미확보' if missing(v) else str(v)
        if key in ['equity','operating_profit','ocf'] and not missing(v):text='음수' if v<0 else '0' if v==0 else '양수'
        facts.append({'항목':label,'값':None if missing(v) else v,'확인 사실':text})
    reasons=[]
    if not cfg.get('approved_by_user') or not cfg.get('approval_reference'):reasons.append('최종 판정 기준·임계값 승인 대기')
    if required:reasons.append('필수 정보 미확보: '+', '.join(required))
    if reasons:return {'status':'판정 보류','reasons':reasons,'facts':facts,'rules_version':cfg['version']}
    hits=[]
    for rule in cfg['rules']:
        value=row.get(rule['field']);state=rule['state']
        if state not in STATES:raise ValueError('Invalid suitability state')
        if not missing(value) and OPS[rule['operator']](value,rule['value']):hits.append(rule)
    status=next((s for s in ['투자 부적격','주의','판정 보류'] if any(r['state']==s for r in hits)), '투자 적격' if cfg.get('eligible_when_no_flags') else '판정 보류')
    return {'status':status,'reasons':[r['reason'] for r in hits] or ['승인된 기준 충족' if status=='투자 적격' else '적격 판정 규칙 미확정'],'facts':facts,'rules_version':cfg['version']}
