"""Package only isolated research artifacts; never include repository credentials."""
from pathlib import Path
import hashlib,json,zipfile
ROOT=Path(__file__).resolve().parent
DEST=ROOT/'full-market-20261019.zip'
def allowed(p):
 rel=p.relative_to(ROOT)
 return p.is_file() and not any(x.startswith('.') or x=='__pycache__' for x in rel.parts) and p!=DEST and p.name not in ['artifact-hashes.json','package-sha256.txt'] and p.suffix not in ['.pyc','.wal','.tmp']
def main():
 files=sorted(p for p in ROOT.rglob('*') if allowed(p))
 hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
 manifest=ROOT/'results/artifact-hashes.json';manifest.write_text(json.dumps(hashes,ensure_ascii=False,indent=2)+'\n');files.append(manifest)
 with zipfile.ZipFile(DEST,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
  for p in files:z.write(p,'full-market/'+str(p.relative_to(ROOT)))
 (ROOT/'results/package-sha256.txt').write_text(hashlib.sha256(DEST.read_bytes()).hexdigest()+'  full-market-20261019.zip\n')
 with zipfile.ZipFile(DEST) as z:
  assert z.testzip() is None
  for name,h in hashes.items():assert hashlib.sha256(z.read('full-market/'+name)).hexdigest()==h
 print(json.dumps({'zip':str(DEST),'files':len(files),'bytes':DEST.stat().st_size,'hash_checks':len(hashes)}))
if __name__=='__main__':main()
