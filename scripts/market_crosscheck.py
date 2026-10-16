from screener.market_validation import validate
def crosscheck(b,reference,scope_evidence=None):
    row=reference.get(b['symbol']);comparison=b.get('price_comparison',{})
    if not row or row.get('BAS_DD')!=b['asof'].replace('-',''):return b
    active={'Same-date KIS / KRX official OHLCV fields differ',
            'KIS raw / independent KRX daily close mismatch',
            'Same-date adjusted / raw KRX close mismatch'}
    previous=list(dict.fromkeys(b.get('price_conflicts',[])))
    b['price_conflicts']=[x for x in previous if x not in active]
    official=float(row['TDD_CLSPRC']);comparison['krx_official_unadjusted']=official
    b['price_crosscheck']={'source':'KRX official daily trading statistics','trade_date':b['asof'],'raw_close_match':comparison.get('kis_unadjusted')==official,'adjusted_latest_equals_raw':comparison.get('kis_adjusted')==official}
    b['price_finality']='CROSS_VALIDATED_DAILY' if b['price_crosscheck']['raw_close_match'] else 'CONFLICT'
    if not b['price_crosscheck']['adjusted_latest_equals_raw']:
        b['price_conflicts'].append('Same-date adjusted / raw KRX close mismatch')
        b['price_finality']='CONFLICT'
    mapping={'open':'TDD_OPNPRC','high':'TDD_HGPRC','low':'TDD_LWPRC','close':'TDD_CLSPRC','volume':'ACC_TRDVOL','turnover':'ACC_TRDVAL'}
    if b.get('bars') and b['bars'][-1]['trade_date']==b['asof'] and all(k in row for k in mapping.values()) and b['price_crosscheck']['adjusted_latest_equals_raw']:
        b['price_crosscheck']['ohlcv_fields_match']={k:b['bars'][-1][k]==float(row[field]) for k,field in mapping.items()}
        if not all(b['price_crosscheck']['ohlcv_fields_match'].values()):
            b['price_conflicts'].append('Same-date KIS / KRX official OHLCV fields differ')
            b['price_finality']='CONFLICT'
    if not b['price_crosscheck']['raw_close_match']:
        if 'KIS raw / independent KRX daily close mismatch' not in b['price_conflicts']:b['price_conflicts'].append('KIS raw / independent KRX daily close mismatch')
    b['price_conflicts']=list(dict.fromkeys(b['price_conflicts']))
    if previous!=b['price_conflicts'] and previous:
        from screener.liquidity_quality import digest
        event={'asof':b['asof'],'previous_reasons':previous,'current_reasons':b['price_conflicts'][:],
               'bars_sha256':digest(b.get('bars',[])),'reference_sha256':digest(row)}
        history=b.setdefault('conflict_history',[])
        if event not in history:history.append(event)
    b['validation']=validate(b,b['asof']);return b
