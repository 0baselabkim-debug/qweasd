"""Invisible public-source identity for distinguishing CI from actual deployment."""
import hashlib,subprocess
from pathlib import Path

def identity(root=None):
 root=Path(root or Path(__file__).resolve().parents[1])
 paths=[root/'app.py',root/'technical_rules.json',root/'eligibility_rules.json',root/'.streamlit/config.toml']
 paths+=list((root/'screener').rglob('*.py'))+list((root/'screener').rglob('*.html'))
 digest=hashlib.sha256()
 for p in sorted(set(paths)):
  if p.is_file():digest.update(str(p.relative_to(root)).encode()+b'\0'+p.read_bytes()+b'\0')
 try:
  revision=subprocess.run(['git','rev-parse','HEAD'],cwd=root,capture_output=True,text=True,timeout=2,check=True).stdout.strip()
  if len(revision)!=40 or any(c not in '0123456789abcdef' for c in revision):revision='unavailable'
 except (OSError,subprocess.SubprocessError):revision='unavailable'
 from .publication import selected
 database=selected(root/'snapshot/dashboard.duckdb')
 return {'source_sha256':digest.hexdigest(),'revision':revision,'data_sha256':hashlib.sha256(database.read_bytes()).hexdigest()}
