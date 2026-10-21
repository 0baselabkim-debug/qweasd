"""Offline exact-date official reconciliation. Original files and rules stay intact."""
from copy import deepcopy
import math
from forward_system import FIELDS, KRX_FIELDS
from price_link import six,valid,action_flag
from conflict_reconciliation import official_rows

def compare_six(original,later,official):
 if official is None or not valid(official):return 'official_unavailable_or_invalid'
 if six(original)==six(official):return 'original_matches'
 if six(later)==six(official):return 'later_matches'
 return 'neither_matches'

def candidate_context(old,record,official,explicit_adjustment=False):
 """Separate candidate: only full, sourced adjusted bars backed by unique KRX.
 Never synthesize a close into a different bar; never infer adjustment factors.
 """
 from price_link import normalized
 new=normalized(record.get('adjusted_prices',[]));candidate=deepcopy(old);audit=[]
 flagged=explicit_adjustment or any(action_flag(r) for r in record.get('adjusted_prices',[]))
 for d in sorted(set(old)&set(new)):
  if six(old[d])==six(new[d]):continue
  if not flagged and official.get(d)=={six(new[d])}:
   candidate[d]=deepcopy(new[d]);audit.append({'date':d,'original':deepcopy(old[d]),'candidate':deepcopy(new[d]),'basis':'complete KIS adjusted bar / exact KRX six-field match','prospective':False})
 return candidate,audit
