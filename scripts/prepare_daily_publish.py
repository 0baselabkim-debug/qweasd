"""Prepare a publishable candidate; original snapshot is untouched on validation failure."""
import argparse,gzip,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from screener.daily import build_candidate,candidate_manifest

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--bundle',type=Path,required=True);p.add_argument('--output',type=Path,default=Path('daily-candidate'));a=p.parse_args()
    b=json.loads(gzip.decompress(a.bundle.read_bytes()))
    if b.get('status')=='closed':print('Exchange closed: published data retained');raise SystemExit(0)
    a.output.mkdir(exist_ok=True)
    audit=build_candidate(b,Path('snapshot/dashboard.duckdb'),a.output/'dashboard.duckdb')
    manifest=candidate_manifest(a.output/'dashboard.duckdb',json.loads(Path('snapshot/manifest.json').read_text()),audit)
    (a.output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    (a.output/'validation.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(audit,ensure_ascii=False))
