"""Read only a generation with matching DB, manifest, validation and ledger hashes."""
import hashlib,json
from pathlib import Path

def coherent(root):
    try:
        manifest=json.loads((root/'manifest.json').read_text());expected=manifest['sha256']
        if hashlib.sha256((root/'dashboard.duckdb').read_bytes()).hexdigest()!=expected:return False
        validation=json.loads((root/'daily-validation.json').read_text())
        ledger=json.loads((root/'publication-ledger.json').read_text())[manifest['asof']]
        return validation['sha256']==ledger['sha256']==manifest['daily_validation']['sha256']==expected
    except (OSError,ValueError,KeyError):return False

def selected(path):
    path=Path(path);root=path.parent
    # Local development/test databases have no publication contract.
    if not (root/'manifest.json').exists():return path
    if coherent(root):return path
    history=root/'publication-history'
    for old in sorted(history.glob('*'),key=lambda p:p.stat().st_mtime_ns,reverse=True):
        if coherent(old):return old/'dashboard.duckdb'
    raise ValueError('Publication generation incomplete; previous valid generation unavailable')

def stamp(path):
    path=Path(path)
    return tuple(p.stat().st_mtime_ns if p.exists() else None for p in [path,path.parent/'manifest.json',path.parent/'daily-validation.json',path.parent/'publication-ledger.json'])
