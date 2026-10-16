"""Physical request counts, separate from logical two-endpoint eligibility."""
from collections import Counter
import re

def summarize(metrics,records):
    symbols=Counter(m['symbol'] for m in metrics if re.fullmatch(r'\d{6}',str(m.get('symbol',''))))
    return {'physical_requests':len(metrics),'authentication':sum(m.get('kind')=='authentication' for m in metrics),
            'retries':sum(m.get('attempt',1)>1 for m in metrics),
            'rate_limit_responses':sum(m.get('http')==429 or m.get('code')=='EGW00201' for m in metrics),
            'endpoint_counts':dict(Counter(m.get('endpoint','legacy') for m in metrics)),
            'per_symbol_requests':dict(symbols),
            'successful_with_two_physical_requests':sum(symbols.get(code)==2 for code in records),
            'successful_without_request_this_attempt':sum(code not in symbols for code in records),
            'more_than_two_request_symbols':{code:n for code,n in symbols.items() if n>2},
            'quote_fallback_reasons':dict(Counter(reason for r in records.values() for reason in r.get('quote_fallback_reasons',[]))),
            'response_seconds':sum(m.get('seconds',0) for m in metrics)}
