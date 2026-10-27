"""Content-addressed immutable originals + generation Parquet + rebuildable DuckDB.

The OS owner can override permissions: this is an append-only application, not
hardware WORM. Every read verifies integrity and never repairs a corrupt original.
"""
import datetime as dt
import gzip
import io
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import duckdb
import pandas as pd
from .common import (ArchiveError, digest, encoded, exclusive_write, fsync_dir,
                     hash_name, latest_time, safe_name, timestamp, utcnow, writer_lock)

FORMAT = dict(format='investment-assets', version=1, original_policy='append-only-content-addressed')
OBS_SCHEMA = dict(observation_id='VARCHAR',generation_id='VARCHAR',channel='VARCHAR',population_version='VARCHAR',
    kind='VARCHAR',entity='VARCHAR',trade_date='DATE',observed_at='TIMESTAMPTZ',available_at='TIMESTAMPTZ',
    ingested_at='TIMESTAMPTZ',observation_time_status='VARCHAR',internal_validation='VARCHAR',
    official_validation='VARCHAR',provider_finality='VARCHAR',historical_backtest_eligible='BOOLEAN',payload='JSON')
FILE_SCHEMA = dict(generation_id='VARCHAR',logical_path='VARCHAR',sha256='VARCHAR',bytes='BIGINT')
POP_SCHEMA = dict(generation_id='VARCHAR',population_version='VARCHAR',profile='VARCHAR',payload='JSON')


def _parquet(path, rows, schema):
    with duckdb.connect(':memory:') as db:
        db.execute('CREATE TABLE output ('+','.join(k+' '+v for k,v in schema.items())+')')
        if rows:
            frame=pd.DataFrame(rows,columns=list(schema));db.register('input_rows',frame)
            db.execute('INSERT INTO output SELECT '+','.join('"'+k+'"' for k in schema)+' FROM input_rows')
        db.execute("COPY output TO '"+str(path).replace("'","''")+"' (FORMAT PARQUET, COMPRESSION ZSTD)")
    path.chmod(0o444)


def _json(path):
    return json.loads(Path(path).read_bytes())


class Archive:
    def __init__(self, root):
        self.root=Path(root).expanduser().resolve()
        # An archive cannot be put anywhere inside an operating Git checkout.
        if any(((p/'.git').is_file() or (p/'.git/HEAD').is_file()) for p in (self.root,*self.root.parents)):
            raise ArchiveError('archive_must_be_outside_git_checkout')
        self.root.mkdir(parents=True,exist_ok=True)
        exclusive_write(self.root/'FORMAT.json',encoded(FORMAT))
        for name in ('objects/sha256','generations','commits'):
            (self.root/name).mkdir(parents=True,exist_ok=True)

    def _object(self, sha):
        hash_name(sha)
        return self.root/'objects/sha256'/sha[:2]/sha

    def _put(self, raw):
        h=digest(raw);exclusive_write(self._object(h),raw)
        return h

    def _generation(self, generation):
        return self.root/'generations'/hash_name(generation)

    def _commit(self, generation):
        return self.root/'commits'/(hash_name(generation)+'.json')

    def _seal_commit(self,generation,clock):
        self.verify_generation(generation,_prepared=True)
        p=self._commit(generation)
        if not p.exists():
            exclusive_write(p,encoded(dict(generation_id=generation,manifest_sha256=digest((self._generation(generation)/'manifest.json').read_bytes()),committed_at=timestamp(clock()).isoformat())))
        return self.verify_generation(generation)

    def _guard_destination(self, destination):
        path=Path(destination).resolve()
        if any(((p/'.git').is_file() or (p/'.git/HEAD').is_file()) for p in (path,*path.parents)):raise ArchiveError('destination_into_git_checkout_forbidden')
        roots=[self.root]
        for p in (self.root/'generations').glob('*/manifest.json'):
            source=_json(p).get('source',{}).get('repository_path')
            if source:roots.append(Path(source).resolve())
        if any(path==r or path.is_relative_to(r) or r.is_relative_to(path) for r in roots):
            raise ArchiveError('destination_overlaps_archive_or_operating_source')
        return path

    def ingest(self, bundle, *, clock=utcnow):
        from .sources import CHANNELS
        if bundle['channel'] not in CHANNELS:
            raise ArchiveError('unknown_source_channel')
        if bundle['provider_finality'] not in ('UNVERIFIED','UNKNOWN'):raise ArchiveError('unsupported_provider_finality_assertion')
        if bundle['archive_status']=='SOURCE_BYTES_UNAVAILABLE' and bundle['observations']:raise ArchiveError('receipt_only_cannot_contain_approved_rows')
        source_root=Path(bundle['source']['repository_path']).resolve()
        if self.root==source_root or self.root.is_relative_to(source_root) or source_root.is_relative_to(self.root):
            raise ArchiveError('archive_source_overlap')
        dt.date.fromisoformat(bundle['asof'])
        files=bundle['files']
        if not files:raise ArchiveError('missing_original_files')
        inventory=[dict(logical_path=safe_name(p),sha256=digest(b),bytes=len(b)) for p,b in sorted(files.items())]
        identity=dict(channel=bundle['channel'],asof=bundle['asof'],adapter_version=1,
            adapter_sha256={n:digest((Path(__file__).parent/n).read_bytes()) for n in ('archive.py','sources.py','common.py')},
            source={k:v for k,v in bundle['source'].items() if k!='repository_path'},
            population_version=digest(encoded(bundle['population'])),files=inventory,archive_status=bundle['archive_status'])
        generation=digest(encoded(identity));folder=self._generation(generation)
        with writer_lock(self.root):
            if folder.exists():
                old=self._seal_commit(generation,clock)
                if old['identity']!=identity:raise ArchiveError('generation_identity_collision')
                self._catalog()
                return dict(status='ALREADY_PRESENT',generation_id=generation,committed_at=old['committed_at'],**_json(folder/'summary.json'))
            for raw in files.values():self._put(raw)
            ingested=timestamp(clock()).isoformat()
            available=bundle.get('available_at')
            if available:available=timestamp(available).isoformat()
            observations=[];seen=set()
            for r in bundle['observations']:
                dt.date.fromisoformat(r['trade_date'])
                if r['trade_date']>bundle['asof']:raise ArchiveError('future_observation_date')
                h=digest(encoded(r))
                if h in seen:raise ArchiveError('duplicate_observation_not_silently_removed')
                seen.add(h)
                if r.get('provider_finality') not in ('UNVERIFIED','UNKNOWN'):
                    raise ArchiveError('unsupported_provider_finality_assertion')
                observations.append(dict(r,observation_id=h,generation_id=generation,channel=bundle['channel'],
                    population_version=identity['population_version'],available_at=latest_time(available,r.get('observed_at')),
                    ingested_at=ingested,payload=encoded(r['payload']).decode()))
            summary=dict(channel=bundle['channel'],asof=bundle['asof'],archive_status=bundle['archive_status'],
                archived_original_files=len(inventory),archived_original_bytes=sum(x['bytes'] for x in inventory),
                archived_observations=len(observations),source_reported_target=bundle.get('source_reported_target'),
                unknown_observation_times=sum(r['observation_time_status']=='UNKNOWN' for r in observations),
                kinds={k:sum(r['kind']==k for r in observations) for k in sorted({r['kind'] for r in observations})},
                source_internal_validation=bundle['source_internal_validation'],flow_validation=bundle['flow_validation'],
                price_validation=bundle['price_validation'],provider_finality=bundle['provider_finality'],
                historical_backtest_eligible=bundle.get('historical_backtest_eligible'),ingested_at=ingested,
                source_available_at=available,warnings=bundle.get('warnings',[]))
            manifest={k:v for k,v in bundle.items() if k not in ('files','observations','population')}
            manifest.update(schema=1,generation_id=generation,identity=identity,population_version=identity['population_version'],
                ingested_at=ingested,available_at=available,files=inventory)
            staging=Path(tempfile.mkdtemp(prefix='.pending-',dir=self.root))
            try:
                _parquet(staging/'observations.parquet',observations,OBS_SCHEMA)
                _parquet(staging/'files.parquet',[dict(f,generation_id=generation) for f in inventory],FILE_SCHEMA)
                _parquet(staging/'population.parquet',[dict(generation_id=generation,population_version=identity['population_version'],
                    profile=bundle['population']['profile'],payload=encoded(bundle['population']).decode())],POP_SCHEMA)
                exclusive_write(staging/'summary.json',encoded(summary))
                manifest['derived_files']={p.name:digest(p.read_bytes()) for p in staging.iterdir() if p.is_file()}
                exclusive_write(staging/'manifest.json',encoded(manifest))
                exclusive_write(staging/'manifest.sha256',(digest(encoded(manifest))+'\n').encode())
                for p in staging.iterdir():
                    with p.open('rb') as f:os.fsync(f.fileno())
                fsync_dir(staging)
                os.rename(staging,folder);folder.chmod(0o555);fsync_dir(folder.parent)
            except Exception:
                if staging.exists():
                    staging.chmod(0o755);shutil.rmtree(staging)
                raise
            committed=self._seal_commit(generation,clock)
            self._catalog()
            return dict(status='IMPORTED',generation_id=generation,committed_at=committed['committed_at'],**summary)

    def verify_generation(self, generation, *, _prepared=False):
        folder=self._generation(generation)
        if digest((folder/'manifest.json').read_bytes())!=(folder/'manifest.sha256').read_text().strip():raise ArchiveError('manifest_checksum_mismatch')
        manifest=_json(folder/'manifest.json')
        if manifest['generation_id']!=generation or digest(encoded(manifest['identity']))!=generation:
            raise ArchiveError('generation_manifest_identity_mismatch')
        if manifest['files']!=manifest['identity']['files']:
            raise ArchiveError('manifest_inventory_identity_mismatch')
        expected={'manifest.json','manifest.sha256',*manifest['derived_files']}
        if {p.name for p in folder.iterdir()}!=expected:
            raise ArchiveError('generation_extra_or_missing_file')
        for name,h in manifest['derived_files'].items():
            safe_name(name);p=folder/name
            if p.is_symlink() or digest(p.read_bytes())!=h:
                raise ArchiveError('derived_file_hash_mismatch')
        for f in manifest['files']:
            safe_name(f['logical_path']);p=self._object(f['sha256'])
            if p.is_symlink() or not p.exists() or p.stat().st_size!=f['bytes'] or digest(p.read_bytes())!=f['sha256']:
                raise ArchiveError('original_object_missing_or_corrupt')
        with duckdb.connect(':memory:') as db:
            file_rows=db.execute('SELECT logical_path,sha256,bytes FROM read_parquet(?) ORDER BY logical_path',[str(folder/'files.parquet')]).fetchall()
            if file_rows!=[(r['logical_path'],r['sha256'],r['bytes']) for r in manifest['files']]:
                raise ArchiveError('files_parquet_manifest_mismatch')
            pop=db.execute('SELECT population_version,payload FROM read_parquet(?)',[str(folder/'population.parquet')]).fetchall()
            if len(pop)!=1 or pop[0][0]!=manifest['population_version'] or digest(encoded(json.loads(pop[0][1])))!=manifest['population_version']:
                raise ArchiveError('population_version_mismatch')
            count,bad,duplicates=db.execute('''SELECT count(*),count(*) FILTER(WHERE generation_id!=? OR channel!=? OR population_version!=? OR provider_finality NOT IN ('UNVERIFIED','UNKNOWN')),
                count(*)-count(distinct observation_id) FROM read_parquet(?)''',
                [generation,manifest['channel'],manifest['population_version'],str(folder/'observations.parquet')]).fetchone()
            s=_json(folder/'summary.json')
            if s['source_available_at']!=manifest['available_at'] or s['ingested_at']!=manifest['ingested_at']:raise ArchiveError('generation_time_metadata_mismatch')
            if count!=s['archived_observations'] or bad or duplicates:
                raise ArchiveError('observation_parquet_mismatch')
        if not _prepared:
            if not self._commit(generation).exists():raise ArchiveError('generation_not_committed')
            commit=_json(self._commit(generation))
            if commit.get('generation_id')!=generation or commit.get('manifest_sha256')!=digest((folder/'manifest.json').read_bytes()):raise ArchiveError('commit_receipt_hash_mismatch')
            if timestamp(commit['committed_at'])<timestamp(manifest['ingested_at']):raise ArchiveError('commit_time_before_ingestion')
            manifest=dict(manifest,committed_at=timestamp(commit['committed_at']).isoformat())
        return manifest

    def _catalog(self):
        """Replace only a derived cache. Originals and generation files are immutable."""
        manifests=[self.verify_generation(p.name) for p in sorted((self.root/'generations').iterdir()) if self._commit(p.name).exists()]
        path=self.root/('.catalog-'+str(os.getpid())+'.duckdb')
        try:
            with duckdb.connect(str(path)) as db:
                db.execute('CREATE TABLE generations(generation_id VARCHAR, channel VARCHAR, "asof" DATE, available_at TIMESTAMPTZ, ingested_at TIMESTAMPTZ, committed_at TIMESTAMPTZ, population_version VARCHAR, archive_status VARCHAR)')
                if manifests:
                    db.executemany('INSERT INTO generations VALUES (?,?,?,?,?,?,?,?)',[[m[k] for k in ('generation_id','channel','asof','available_at','ingested_at','committed_at','population_version','archive_status')] for m in manifests])
                    for table in ('observations','files','population'):
                        paths=[str(self._generation(m['generation_id'])/(table+'.parquet')) for m in manifests]
                        quoted=','.join("'"+s.replace("'","''")+"'" for s in paths)
                        db.execute(f'CREATE VIEW {table} AS SELECT * FROM read_parquet([{quoted}])')
                db.execute('CHECKPOINT')
            os.replace(path,self.root/'catalog.duckdb');fsync_dir(self.root)
        finally:
            path.unlink(missing_ok=True)

    def verify(self):
        prepared=[p.name for p in (self.root/'generations').iterdir() if not self._commit(p.name).exists()]
        results=[self.verify_generation(p.name) for p in sorted((self.root/'generations').iterdir()) if self._commit(p.name).exists()]
        for p in (self.root/'objects').rglob('*'):
            if p.is_symlink():raise ArchiveError('archive_symlink_forbidden')
            if p.is_file() and (hash_name(p.name)!=digest(p.read_bytes()) or p.parent.name!=p.name[:2]):raise ArchiveError('original_object_missing_or_corrupt')
        return dict(status='INCOMPLETE' if prepared else 'PASS',prepared_uncommitted=prepared,generations=len(results),unique_original_objects=sum(p.is_file() for p in (self.root/'objects').rglob('*')),
                    archived_observations=sum(_json(self._generation(m['generation_id'])/'summary.json')['archived_observations'] for m in results))

    def rebuild_catalog(self):
        with writer_lock(self.root):self._catalog()
        return dict(status='REBUILT_FROM_VERIFIED_PARQUET')

    def query(self, *, channel, at, knowledge_at=None, generation=None, kind=None, entity=None, asof=None, limit=100, reproducible_only=False):
        from .sources import CHANNELS
        if channel not in CHANNELS:raise ArchiveError('explicit_single_source_channel_required')
        when=timestamp(at);knowledge=timestamp(knowledge_at) if knowledge_at else when
        if type(limit) is not int or not 1<=limit<=10000:raise ArchiveError('query_limit_out_of_range')
        candidates=[]
        for p in sorted((self.root/'generations').iterdir()):
            if not self._commit(p.name).exists():continue
            m=self.verify_generation(p.name)
            if m['channel']!=channel or generation and generation!=p.name or asof and asof!=m['asof']:continue
            if m['available_at'] and timestamp(m['available_at'])<=when and timestamp(m['committed_at'])<=knowledge:candidates.append(m)
        if not candidates:return dict(status='NO_ELIGIBLE_GENERATION',rows=[],source_cutoff=when.isoformat(),archive_knowledge_cutoff=knowledge.isoformat())
        newest=max(timestamp(m['available_at']) for m in candidates)
        matches=[m for m in candidates if timestamp(m['available_at'])==newest]
        if len(matches)!=1:raise ArchiveError('ambiguous_generation_pin_required')
        m=matches[0];folder=self._generation(m['generation_id'])
        # DuckDB's Python timezone decoder optionally requires pytz. Keep the
        # existing environment unchanged and return explicit UTC text instead.
        columns=','.join((f'CAST("{k}" AS VARCHAR) AS "{k}"' if t=='TIMESTAMPTZ' else f'"{k}"') for k,t in OBS_SCHEMA.items())
        sql='SELECT '+columns+' FROM read_parquet(?) WHERE (available_at IS NOT NULL AND available_at<=?) AND ingested_at<=?'
        args=[str(folder/'observations.parquet'),when,knowledge]
        for col,v in [('kind',kind),('entity',entity)]:
            if v is not None:sql+=' AND '+col+'=?';args.append(v)
        if reproducible_only:sql+=" AND observation_time_status='KNOWN' AND historical_backtest_eligible IS NOT FALSE"
        with duckdb.connect(':memory:') as db:
            total=db.execute('SELECT count(*) FROM ('+sql+')',args).fetchone()[0]
            cur=db.execute(sql+' ORDER BY trade_date,kind,entity,observation_id LIMIT ?',args+[limit])
            names=[x[0] for x in cur.description];rows=[]
            for values in cur.fetchall():
                r={k:v.isoformat() if hasattr(v,'isoformat') else v for k,v in zip(names,values)}
                for field in ('observed_at','available_at','ingested_at'):
                    if r[field] is not None:r[field]=timestamp(r[field]).isoformat()
                r['payload']=json.loads(r['payload']);r['observation_time_reproducible']=r['observation_time_status']=='KNOWN'
                rows.append(r)
        return dict(status='FOUND',generation_id=m['generation_id'],channel=channel,population_version=m['population_version'],
            source_cutoff=when.isoformat(),archive_knowledge_cutoff=knowledge.isoformat(),
            replay_mode='ARCHIVE_AS_KNOWN' if knowledge<=when else 'EXPLICIT_HISTORICAL_RECONSTRUCTION',
            historical_backtest_eligible=m.get('historical_backtest_eligible'),archive_status=m['archive_status'],total_rows=total,rows=rows)

    def restore_generation(self, generation, destination):
        m=self.verify_generation(generation);dest=self._guard_destination(destination)
        if dest.exists():raise ArchiveError('restore_destination_already_exists')
        dest.parent.mkdir(parents=True,exist_ok=True)
        stage=Path(tempfile.mkdtemp(prefix='.asset-restore-',dir=dest.parent))
        try:
            for f in m['files']:
                p=stage/safe_name(f['logical_path']);exclusive_write(p,self._object(f['sha256']).read_bytes())
            os.rename(stage,dest);fsync_dir(dest.parent)
        finally:
            if stage.exists():shutil.rmtree(stage)
        return dict(status='RESTORED_EXACT_ORIGINAL_BYTES',generation_id=generation,destination=str(dest),files=len(m['files']),archive_status=m['archive_status'])

    def backup(self, destination):
        dest=self._guard_destination(destination)
        if dest.exists():raise ArchiveError('backup_destination_already_exists')
        dest.parent.mkdir(parents=True,exist_ok=True)
        with writer_lock(self.root):
            if self.verify()['status']!='PASS':raise ArchiveError('backup_requires_committed_generations')
            paths=[self.root/'FORMAT.json']+[p for top in ('objects','generations','commits') for p in (self.root/top).rglob('*') if p.is_file()]
            with dest.open('xb') as out:
                with gzip.GzipFile(fileobj=out,mode='wb',mtime=0,filename='') as gz:
                    with tarfile.open(fileobj=gz,mode='w|') as tar:
                        for p in sorted(paths):
                            if p.is_symlink():raise ArchiveError('backup_symlink_forbidden')
                            raw=p.read_bytes();info=tarfile.TarInfo(str(p.relative_to(self.root)));info.size=len(raw);info.mode=0o444
                            tar.addfile(info,io.BytesIO(raw))
                out.flush();os.fsync(out.fileno())
            dest.chmod(0o444);h=digest(dest.read_bytes());exclusive_write(Path(str(dest)+'.sha256'),(h+'\n').encode())
        return dict(status='BACKUP_CREATED',path=str(dest),sha256=h,bytes=dest.stat().st_size,
                    durability='Local backup; independent storage durability requires an external persistent volume/object store.')

    @classmethod
    def restore_backup(cls, archive, destination, expected_sha256, *, max_bytes=2_000_000_000):
        archive=Path(archive);hash_name(expected_sha256)
        if digest(archive.read_bytes())!=expected_sha256:raise ArchiveError('backup_digest_mismatch')
        dest=Path(destination).resolve()
        if dest.exists():raise ArchiveError('restore_destination_already_exists')
        if any(((p/'.git').is_file() or (p/'.git/HEAD').is_file()) for p in (dest,*dest.parents)):raise ArchiveError('restore_into_git_checkout_forbidden')
        dest.parent.mkdir(parents=True,exist_ok=True);stage=Path(tempfile.mkdtemp(prefix='.asset-backup-restore-',dir=dest.parent))
        try:
            seen=set();total=0
            with tarfile.open(archive,'r:gz') as tar:
                for member in tar:
                    name=safe_name(member.name)
                    if not member.isfile() or name in seen:raise ArchiveError('unsafe_backup_member')
                    if name!='FORMAT.json' and not name.startswith(('objects/sha256/','generations/','commits/')):raise ArchiveError('unexpected_backup_member')
                    seen.add(name);total+=member.size
                    if total>max_bytes:raise ArchiveError('backup_uncompressed_size_limit')
                    exclusive_write(stage/name,tar.extractfile(member).read())
            if _json(stage/'FORMAT.json')!=FORMAT:raise ArchiveError('unsupported_archive_format')
            restored=cls(stage)
            if restored.verify()['status']!='PASS':raise ArchiveError('backup_contains_uncommitted_generation')
            restored.rebuild_catalog()
            os.rename(stage,dest);fsync_dir(dest.parent)
            # DuckDB absolute view paths belong to the final location.
            final=cls(dest);final.rebuild_catalog()
            return dict(**final.verify(),operation='BACKUP_RESTORED_AND_VERIFIED',destination=str(dest))
        finally:
            if stage.exists():shutil.rmtree(stage)
