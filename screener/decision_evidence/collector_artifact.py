"""Read-only, digest-pinned collector artifact inspection; never calls a provider."""
import json
import stat
import zipfile
from pathlib import Path, PurePosixPath
from screener.decision_support.evidence import encoded, sha

RUN='40882669831'
ARTIFACT='12910068161'
ZIP_SHA='4682801c9d88a5fb43496c55263e66402aef42f089f8987d667458d15cf8e8c7'
MANIFEST_SHA='55d41a0dbb9dc3e9d2022ba676eb7039172b5914df5843c0b114f0b29710e812'
ASOF='2026-10-29'
GENERATION='kis-authorized-20261029-'+RUN


def verify_directory(root,expected_manifest_sha=MANIFEST_SHA):
    root=Path(root);raw=(root/'manifest.json').read_bytes()
    if sha(raw)!=expected_manifest_sha:raise ValueError('artifact_manifest_anchor_mismatch')
    manifest=json.loads(raw);files={}
    for p in root.rglob('*'):
        if p.is_symlink():raise ValueError('artifact_directory_symlink')
        if p.is_file():files[str(p.relative_to(root))]=sha(p.read_bytes())
    expected={**manifest['files'],'manifest.json':expected_manifest_sha}
    if files!=expected:raise ValueError('artifact_directory_population_or_hash_mismatch')
    return files


def extract(archive, destination, expected_sha=ZIP_SHA, expected_manifest_sha=MANIFEST_SHA):
    archive=Path(archive);destination=Path(destination)
    if archive.stat().st_size>256*1024**2 or sha(archive.read_bytes())!=expected_sha:
        raise ValueError('artifact_ZIP_digest_mismatch')
    if destination.exists():raise ValueError('artifact_destination_must_be_new')
    with zipfile.ZipFile(archive) as z:
        infos=z.infolist();files=[i for i in infos if not i.is_dir()]
        if len(infos)>30000 or sum(i.file_size for i in infos)>1024**3:raise ValueError('artifact_size_limit')
        names=[i.filename for i in files]
        if len(names)!=len(set(names)):raise ValueError('artifact_duplicate_path')
        for i in infos:
            p=PurePosixPath(i.filename)
            if (p.is_absolute() or '..' in p.parts or '\\' in i.filename or i.file_size>128*1024**2
                    or stat.S_ISLNK(i.external_attr>>16)):
                raise ValueError('artifact_unsafe_member')
        raw=z.read('manifest.json');m=json.loads(raw)
        if sha(raw)!=expected_manifest_sha:raise ValueError('artifact_manifest_anchor_mismatch')
        if (m['asof'],m['run_id'],m['generation_id'])!=(ASOF,RUN,GENERATION):raise ValueError('artifact_manifest_identity_mismatch')
        if set(names)!=set(m['files'])|{'manifest.json'}:raise ValueError('artifact_file_population_mismatch')
        for i in files:
            content=z.read(i)
            if i.filename!='manifest.json' and sha(content)!=m['files'][i.filename]:raise ValueError('artifact_member_hash_mismatch')
        destination.mkdir(parents=True)
        for i in files:
            path=destination/i.filename;path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('xb') as f:f.write(z.read(i))
    return dict(zip_sha256=expected_sha,manifest_sha256=sha(raw),files_verified=len(files),zip_bytes=archive.stat().st_size)


def inspect(root):
    from collector_v1.core import Store
    from collector_v1.experiment.engine import replay, qualify_kis, identity
    from collector_v1.providers import task
    root=Path(root);before=verify_directory(root)
    report=json.loads((root/'report.json').read_bytes());context=json.loads((root/'context.json').read_bytes())
    if (context['asof'],context['run_id'],context['generation_id'])!=(ASOF,RUN,GENERATION):raise ValueError('collector_context_mismatch')
    if (report['status'],report['target'],report['kis_validated'],report['all_http_resolved'],report['provider_finality'],report['official_price_status'])!=('INTERNAL_PASS',2346,2346,True,'UNVERIFIED','PENDING_KRX'):
        raise ValueError('collector_report_not_expected_complete_provisional')
    from collector_v1.regular.collector import verify
    verify(root/GENERATION,context,report)
    store=Store(root/GENERATION/'collection');cache=replay(store,1,asof=ASOF,generation_id=GENERATION)
    rows=report['rows']
    if len(rows)!=2346 or len({r['symbol'] for r in rows})!=2346:raise ValueError('collector_population_mismatch')
    for row in rows:
        if qualify_kis(row,cache,store,1,asof=ASOF,generation_id=GENERATION)!=row:raise ValueError('collector_raw_row_mismatch')
    samples={}
    for symbol in ('000720','064400','300080'):
        row=next(r for r in rows if r['symbol']==symbol);values={}
        for kind in ('flow','price'):
            e=cache[identity(task(kind,ASOF,round=1,symbol=symbol,market=row['market'],generation_id=GENERATION))]
            obj=json.loads(store.raw(e));history=obj['output2']
            values[kind]=dict(evidence=e,keys=list(obj),output1=obj.get('output1'),row_count=len(history),
                dates=[x['stck_bsop_date'] for x in history],first_rows=history[:3],last_rows=history[-3:])
        samples[symbol]=dict(current=row,raw=values)
    from collector_v1.experiment.storage import events
    http=events(store.root/'http.jsonl');starts=[e for e in http if e['event']=='started'];ends=[e for e in http if e['event']=='finished']
    after={str(p.relative_to(root)):sha(p.read_bytes()) for p in root.rglob('*') if p.is_file()}
    if before!=after:raise ValueError('original_artifact_changed_during_inspection')
    return dict(status='SOURCE_INSPECTED_NOT_PRICE_APPROVED',asof=ASOF,target=len(rows),context=context,
        report_fields={k:v for k,v in report.items() if k not in ('rows','master','target_symbols','indices','context')},
        samples=samples,source_generation_id=GENERATION,collection_files=[p.name for p in store.root.iterdir()],
        root_files=[p.name for p in root.iterdir()],collection_http_started=len(starts),collection_http_finished=len(ends),
        ledger_first_start=starts[0],ledger_last_end=ends[-1],
        original_dataset_unchanged=True,source_files_checked=len(before),source_DB_JSON_match=True,price_approval='PENDING_KRX',additional_KIS_calls=0)
