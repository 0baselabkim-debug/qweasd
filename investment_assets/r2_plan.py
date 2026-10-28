"""Offline inventory and incremental planning only. No R2 client or uploader."""
import argparse
import json
from pathlib import Path
from .archive import Archive, FORMAT
from .common import ArchiveError, digest, encoded, exclusive_write, hash_name, safe_name, writer_lock
from .stabilization import file_digest

SCHEMA='investment-assets-local-inventory-v1'


def _allowed(path):
    if not isinstance(path,str):raise ArchiveError('invalid_inventory_path')
    safe_name(path)
    return path=='FORMAT.json' or path.startswith(('objects/sha256/','generations/','commits/'))


def verify_inventory(value, *, expected_id=None):
    if not isinstance(value,dict) or set(value)!= {'schema','archive_format','files','generations','inventory_id'}:
        raise ArchiveError('invalid_inventory_schema')
    if value['schema']!=SCHEMA or value['archive_format']!=FORMAT:raise ArchiveError('unsupported_inventory_format')
    hash_name(value['inventory_id'])
    identity={k:v for k,v in value.items() if k!='inventory_id'}
    if digest(encoded(identity))!=value['inventory_id'] or expected_id is not None and expected_id!=value['inventory_id']:
        raise ArchiveError('inventory_digest_mismatch')
    files=value['files']
    if not isinstance(files,list) or not files:raise ArchiveError('inventory_files_missing')
    seen=set();hash_sizes={}
    for f in files:
        if not isinstance(f,dict) or set(f)!={'path','sha256','bytes'}:raise ArchiveError('invalid_inventory_file')
        if not _allowed(f['path']) or f['path'] in seen:raise ArchiveError('unsafe_or_duplicate_inventory_path')
        seen.add(f['path']);hash_name(f['sha256'])
        if type(f['bytes']) is not int or f['bytes']<0:raise ArchiveError('invalid_inventory_size')
        if f['sha256'] in hash_sizes and hash_sizes[f['sha256']]!=f['bytes']:raise ArchiveError('inconsistent_inventory_hash_size')
        hash_sizes[f['sha256']]=f['bytes']
    if files!=sorted(files,key=lambda f:f['path']):raise ArchiveError('inventory_order_not_canonical')
    generations=value['generations']
    if not isinstance(generations,list) or not generations:raise ArchiveError('inventory_generations_missing')
    ids=[]
    for g in generations:
        if not isinstance(g,dict) or set(g)!={'generation_id','channel','asof','provider_finality','archive_status'}:raise ArchiveError('invalid_inventory_generation')
        hash_name(g['generation_id']);ids.append(g['generation_id'])
        if g['provider_finality'] not in ('UNVERIFIED','UNKNOWN'):raise ArchiveError('inventory_finality_not_supported')
        # A complete archive may legitimately retain a receipt-only generation.
        required=['commits/'+g['generation_id']+'.json','generations/'+g['generation_id']+'/manifest.json']
        if any(p not in seen for p in required):raise ArchiveError('inventory_generation_dependency_missing')
    if ids!=sorted(set(ids)):raise ArchiveError('inventory_generation_order_or_duplicates')
    return value


def snapshot_inventory(root):
    root=Path(root).resolve()
    if not root.is_dir() or not (root/'FORMAT.json').is_file():raise ArchiveError('existing_archive_required')
    a=Archive(root)
    with writer_lock(a.root):
        status=a.verify()
        if status['status']!='PASS' or not status['generations']:raise ArchiveError('complete_committed_archive_required')
        paths=[a.root/'FORMAT.json']
        for top in ('objects','generations','commits'):
            for p in (a.root/top).rglob('*'):
                if p.is_symlink():raise ArchiveError('inventory_symlink_forbidden')
                if p.is_file():paths.append(p)
        files=[dict(path=str(p.relative_to(a.root)),sha256=file_digest(p),bytes=p.stat().st_size) for p in sorted(paths)]
        generations=[]
        for p in sorted((a.root/'generations').iterdir()):
            m=a.verify_generation(p.name)
            generations.append({k:m[k] for k in ('generation_id','channel','asof','provider_finality','archive_status')})
        value=dict(schema=SCHEMA,archive_format=FORMAT,files=files,generations=generations)
        value['inventory_id']=digest(encoded(value))
        return verify_inventory(value)


def incremental_plan(current, previous=None):
    verify_inventory(current)
    if previous is not None:verify_inventory(previous)
    old={f['path']:f for f in previous['files']} if previous else {}
    new={f['path']:f for f in current['files']}
    if set(old)-set(new):raise ArchiveError('previous_archive_history_missing_no_auto_delete')
    if any(new[p]!=f for p,f in old.items()):raise ArchiveError('immutable_source_changed_no_overwrite')
    if previous:
        curr={g['generation_id']:g for g in current['generations']}
        if any(curr.get(g['generation_id'])!=g for g in previous['generations']):raise ArchiveError('previous_generation_metadata_changed')
    known={f['sha256'] for f in old.values()};blobs={}
    for f in new.values():
        if f['sha256'] not in known:
            blobs.setdefault(f['sha256'],dict(sha256=f['sha256'],bytes=f['bytes'],source_paths=[]))['source_paths'].append(f['path'])
    return dict(schema=1,status='LOCAL_INCREMENT_PLAN_NOT_REMOTE_COMPLETION',inventory_id=current['inventory_id'],
        previous_inventory_id=previous['inventory_id'] if previous else None,
        total_generations=len(current['generations']),total_logical_files=len(new),
        local_new_logical_files=len(set(new)-set(old)),new_unique_plaintext_blobs=len(blobs),
        planned_plaintext_payload_bytes=sum(f['bytes'] for f in blobs.values()),
        reused_plaintext_hashes=len({f['sha256'] for f in new.values()} & known),
        blobs=[blobs[h] for h in sorted(blobs)],
        remote_presence_verified=False,remote_restore_verified=False,upload_authorized=False,external_upload_performed=False,
        encrypted_payload_bytes=None,exclusions=['catalog.duckdb','writer.lock'],
        requirements=['Previous inventory is a local delta baseline, never proof of uploaded objects.',
            'Before remote reuse, authenticate checkpoint identity and verify referenced remote ciphertext.',
            'Encrypt each unique payload for a pinned recipient key version; retain verified ciphertext mapping.',
            'Create payloads/checkpoints conditionally; never overwrite, delete, or move old generations.',
            'Publish an encrypted checkpoint only after every referenced payload is verified.'])


def write_local_plan(root,destination,*,previous_path=None,previous_id=None):
    if bool(previous_path)!=bool(previous_id):raise ArchiveError('previous_inventory_and_trusted_id_required_together')
    previous=json.loads(Path(previous_path).read_bytes()) if previous_path else None
    if previous is not None:verify_inventory(previous,expected_id=previous_id)
    inventory=snapshot_inventory(root);plan=incremental_plan(inventory,previous)
    a=Archive(root);dest=a._guard_destination(destination)
    # No auto-replacement even for a corrupt existing plan.
    exclusive_write(dest,encoded(inventory))
    summary={k:v for k,v in plan.items() if k!='blobs'}
    exclusive_write(Path(str(dest)+'.plan.json'),encoded(plan))
    return dict(summary,inventory_path=str(dest),inventory_bytes=dest.stat().st_size,
        inventory_file_sha256=file_digest(dest),local_plan_written=True)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',required=True);p.add_argument('--destination',required=True)
    p.add_argument('--previous-inventory');p.add_argument('--previous-id')
    args=p.parse_args(argv)
    try:
        result=write_local_plan(args.root,args.destination,previous_path=args.previous_inventory,previous_id=args.previous_id)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except (ArchiveError,OSError,ValueError,KeyError,TypeError):
        print(json.dumps(dict(status='FAIL',reason='local_inventory_or_plan_validation_failed',external_upload_performed=False)));return 1


if __name__=='__main__':raise SystemExit(main())
