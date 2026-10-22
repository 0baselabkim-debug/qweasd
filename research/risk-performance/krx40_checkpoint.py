"""Read the published day's existing Git checkpoint, never a market API."""
import gzip
import json
import subprocess
from pathlib import Path

from krx40_collect import OUT, REPO, git, utcnow
from krx_validation import canonical, hash_bytes, write_once
from forward_system import load


def validate_checkpoint(obj, manifest, symbols):
    day = manifest['asof'].replace('-', '')
    if (obj.get('trade_date') != day or obj.get('exchange') != 'KRX'
            or obj.get('status') != 'complete' or obj.get('failures')
            or str(obj.get('source_run')) != str(manifest.get('daily_run'))
            or not manifest.get('daily_run') or set(obj.get('records', {})) != set(symbols)):
        raise ValueError('published_checkpoint_identity_or_completion_mismatch')
    for symbol, r in obj['records'].items():
        if (r.get('flow', {}).get('stck_bsop_date') != day
                or r.get('request_scope') != {'market': 'J', 'symbol': symbol, 'end': day, 'adjustment': '0'}
                or r.get('chart_header', {}).get('stck_shrn_iscd') != symbol):
            raise ValueError('published_checkpoint_record_identity_mismatch')


def captured_paths():
    paths = []
    for meta in sorted((OUT/'daily-checkpoints').glob('*/*.source.json')):
        x = load(meta); p = (REPO/x['file']).resolve()
        if not p.is_relative_to((OUT/'daily-checkpoints').resolve()) or hash_bytes(p.read_bytes()) != x['sha256']:
            raise ValueError('captured_checkpoint_hash_mismatch')
        paths.append(p)
    return paths


def capture_latest(snapshot, symbols):
    manifest = load(snapshot/'manifest.json'); day = manifest['asof'].replace('-', '')
    pointer = snapshot/'checkpoint-source.json'
    if pointer.exists():
        source = load(pointer); p = (REPO/source['file']).resolve()
        if not p.is_relative_to((OUT/'daily-checkpoints').resolve()) or hash_bytes(p.read_bytes()) != source['sha256']:
            raise ValueError('captured_checkpoint_hash_mismatch')
        validate_checkpoint(load(p), manifest, symbols)
        return p
    ref = 'data/daily-'+day
    subprocess.run(['git', 'fetch', 'origin', 'refs/heads/'+ref], cwd=REPO, check=True, capture_output=True)
    commit = git('rev-parse', 'FETCH_HEAD')
    raw = subprocess.check_output(['git', 'show', commit+':daily-results/bundle.json.gz'], cwd=REPO)
    obj = json.loads(gzip.decompress(raw))
    validate_checkpoint(obj, manifest, symbols)
    path = OUT/'daily-checkpoints'/day/(commit+'.json.gz')
    source = {'source_branch': ref, 'source_commit': commit, 'file': str(path.relative_to(REPO)),
              'sha256': hash_bytes(raw), 'trade_date': manifest['asof'], 'captured_at': utcnow().isoformat(),
              'source_run': obj['source_run'], 'operation': 'Git_read_only_no_market_request'}
    write_once(path, raw)
    write_once(path.with_suffix('').with_suffix('.source.json'), canonical(source))
    write_once(pointer, canonical(source))
    return path
