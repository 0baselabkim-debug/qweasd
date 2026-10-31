import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from screener.historical_v2.kis_recovery import main
if __name__=='__main__':
    import json
    # The approved one-off allowance has been physically spent. Fail closed if
    # its continuation receipt is missing; a new root/control is not a reset.
    receipt=Path(__file__).resolve().parents[1]/'reports/historical-v2-recovery/kis-recovery-budget.json'
    if not receipt.is_file():raise SystemExit('BLOCKED: durable prior recovery budget receipt required')
    budget=json.loads(receipt.read_bytes())
    if budget.get('source_artifact_id')!='12983267630' or budget.get('remaining_additional_http')!=0:
        raise SystemExit('BLOCKED: a new allowance requires separate reviewed atomic reservation; do not reset prior requests')
    raise SystemExit('BLOCKED: one-off KIS recovery allowance exhausted; 3 new requests and 4696 cumulative requests preserved')
