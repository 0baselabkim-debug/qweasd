"""Inventory original bytes without interpreting a successful run as valid data."""
import gzip
import hashlib
import json
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath

from screener.decision_support.evidence import encoded, sha


def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1024**2), b''): h.update(b)
    return h.hexdigest()


def members(z):
    infos = z.infolist()
    if len(infos) > 200000 or sum(i.file_size for i in infos) > 8*1024**3:
        raise ValueError('archive_expansion_limit')
    names = set()
    for i in infos:
        p = PurePosixPath(i.filename)
        if (p.is_absolute() or '..' in p.parts or '\\' in i.filename or
                stat.S_ISLNK(i.external_attr >> 16) or i.file_size > 1024**3):
            raise ValueError('unsafe_archive_member')
        if i.is_dir(): continue
        if i.filename in names: raise ValueError('duplicate_archive_member')
        names.add(i.filename)
    return [i for i in infos if not i.is_dir()]


def inspect_zip(path, expected_sha, *, reference_root=None):
    if file_sha(path) != expected_sha: raise ValueError('ZIP_digest_mismatch')
    reference_root = Path(reference_root) if reference_root else None
    hashes = {}; documents = []; refs = []; errors = []; raw_count = 0
    with zipfile.ZipFile(path) as z:
        infos = members(z)
        for i in infos:
            raw = z.read(i); hashes[i.filename] = sha(raw)
            if i.filename.endswith('.gz'):
                try: payload = gzip.decompress(raw)
                except (OSError, EOFError): errors.append({'file':i.filename,'reason':'gzip_corrupt'}); continue
                name = PurePosixPath(i.filename).stem
                if '/raw/' in '/'+i.filename and re.fullmatch('[0-9a-f]{64}', name):
                    raw_count += 1
                    if sha(payload) != name: errors.append({'file':i.filename,'reason':'raw_SHA_mismatch'})
            else: payload = raw
            if i.filename.endswith(('.json','.json.gz')) and len(payload) < 80*1024**2:
                try: obj = json.loads(payload)
                except (ValueError, UnicodeError): continue
                if not isinstance(obj, dict): continue
                if PurePosixPath(i.filename).name in ('report.json','context.json','summary.json','manifest.json','index.json','last-run.json','daily-validation.json'):
                    fields = ('asof','trade_date','generation_id','dataset_id','run_id','status','target','kis_validated','physical_http','total_physical_http','provider_finality','official_price_status','completed_at','actual_started_at','pipeline_completed_at')
                    documents.append({'path':i.filename, **{k:obj[k] for k in fields if k in obj}})
                url = obj.get('url','')
                if url.startswith('https://data-dbg.krx.co.kr/svc/apis/sto/') and isinstance(obj.get('rows'),list):
                    from scripts.market_reference import classify_rows
                    day = obj.get('asof'); market = 'KOSPI' if '/stk_bydd_trd?' in url else 'KOSDAQ' if '/ksq_bydd_trd?' in url else None
                    if not day or market is None: continue
                    status, rows = classify_rows({'OutBlock_1':obj['rows']},day)
                    entry = dict(asof=day,market=market,status=status,rows=len(rows),source_member=i.filename,
                                 source_member_sha256=sha(raw),observed_at=obj.get('observed_at'))
                    if status == 'RECEIVED' and reference_root:
                        content = gzip.compress(encoded(obj),mtime=0); target = reference_root/(sha(content)+'.json.gz')
                        target.parent.mkdir(parents=True,exist_ok=True)
                        if not target.exists():
                            with target.open('xb') as out: out.write(content)
                        elif target.read_bytes()!=content: raise ValueError('reference_content_address_collision')
                        entry['preserved_file'] = str(target); entry['sha256'] = sha(content)
                    refs.append(entry)
        manifests = []
        for n in hashes:
            if not n.endswith('manifest.json'): continue
            try: obj = json.loads(z.read(n))
            except (ValueError, UnicodeError): continue
            if not isinstance(obj,dict) or not isinstance(obj.get('files'),dict): continue
            parent = PurePosixPath(n).parent
            prefix = '' if str(parent)=='.' else str(parent)+'/'
            checked = 0; failed = []
            for name, value in obj['files'].items():
                expected = value if isinstance(value,str) else value.get('sha256') if isinstance(value,dict) else None
                if not isinstance(expected,str) or not re.fullmatch('[0-9a-f]{64}',expected): continue
                candidate = prefix+name
                if hashes.get(candidate) != expected: failed.append(name)
                else: checked += 1
            manifests.append(dict(path=n,sha256=hashes[n],checked=checked,failures=failed,
                                  status='FAIL' if failed else 'HASHES_VERIFIED' if checked else 'NOT_ASSESSED'))
        return dict(zip_sha256=expected_sha,files=len(infos),expanded_bytes=sum(i.file_size for i in infos),
                    member_hashes=hashes,documents=documents,manifests=manifests,raw_hashes_checked=raw_count,
                    integrity_errors=errors,references=refs,
                    status='FAIL' if errors or any(m['failures'] for m in manifests) else 'BYTES_VERIFIED_DATA_NOT_YET_APPROVED')


def extract_verified(path, expected_sha, destination):
    """No overwrite, traversal, symlinks, or extraction before digest checking."""
    destination = Path(destination)
    if destination.exists(): raise ValueError('destination_must_be_new')
    if file_sha(path) != expected_sha: raise ValueError('ZIP_digest_mismatch')
    with zipfile.ZipFile(path) as z:
        infos = members(z); destination.mkdir(parents=True)
        for i in infos:
            out = destination/i.filename; out.parent.mkdir(parents=True,exist_ok=True)
            with out.open('xb') as f: f.write(z.read(i))
    return {str(p.relative_to(destination)):file_sha(p) for p in destination.rglob('*') if p.is_file()}


def select_generation(candidates):
    """Do not silently choose between divergent equally trusted generations."""
    valid = [c for c in candidates if c.get('integrity')=='PASS' and c.get('complete') is True]
    if not valid: return dict(status='HOLD',reason='no_complete_verified_generation',selected=[])
    rank = lambda c: (c.get('six_field_pass',0),c.get('validated',0),c.get('target',0))
    best = max(map(rank,valid)); selected = [c for c in valid if rank(c)==best]
    values = {c['market_payload_sha256'] for c in selected}
    if len(values)!=1: return dict(status='CONFLICT',reason='equally_ranked_market_values_disagree',selected=[])
    return dict(status='SELECTED_EQUIVALENT',selected=sorted(c['generation_id'] for c in selected),
                reason='completeness_integrity_and_exact_price_validation; no latest-file rule')
