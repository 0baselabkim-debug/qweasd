"""Restore one digest-pinned native derived generation into a new local folder."""
import argparse
import json
from pathlib import Path,PurePosixPath
import sys
import tarfile
import re
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from screener.historical_v2.assets import file_sha
from screener.investment_v2.contracts import independent
from screener.decision_evidence.build import verify_output
from screener.investment_v2.pipeline import verify_bundle


def restore(archive,record,destination):
    archive=Path(archive).resolve();destination=independent(destination,[archive])
    if any((p/'.git').exists() for p in (destination,*destination.parents)):raise ValueError('restore_destination_inside_git_checkout')
    if destination.exists():raise ValueError('restore_destination_must_be_new')
    if file_sha(archive)!=record['archive_sha256']:raise ValueError('restore_archive_digest_mismatch')
    with tarfile.open(archive,'r:gz') as tar:
        members=tar.getmembers();names=[m.name for m in members]
        canonical=[str(PurePosixPath(n)) for n in names]
        if len(names)!=len(set(canonical)) or len(names)>60000 or sum(m.size for m in members)>2*1024**3:raise ValueError('restore_duplicate_or_expansion_limit')
        for member in members:
            p=PurePosixPath(member.name)
            if not member.isfile() or p.is_absolute() or '..' in p.parts or '\\' in member.name:raise ValueError('unsafe_restore_member')
        for group in ('evidence','investment'):
            identifier=record[group+'_dataset_id'];anchor=record[group+'_validation']['manifest_sha256']
            if Path(identifier).name!=identifier or '\\' in identifier or not re.fullmatch('[0-9a-f]{64}',anchor):raise ValueError('restore_generation_identity_or_anchor_invalid')
        destination.mkdir(parents=True)
        for member in members:
            path=destination/member.name;path.parent.mkdir(parents=True,exist_ok=True)
            with tar.extractfile(member) as source,path.open('xb') as target:
                for block in iter(lambda:source.read(1024**2),b''):target.write(block)
    evidence=destination/'evidence'/record['evidence_dataset_id'];investment=destination/'investment'/record['investment_dataset_id']
    return dict(status='RESTORED_AND_VERIFIED',asof=record['asof'],job=record['job'],directory=str(destination),
        evidence=verify_output(evidence,record['evidence_validation']['manifest_sha256']),
        investment=verify_bundle(investment,record['investment_validation']['manifest_sha256']),source_archive_unchanged=True,
        operating_publication='NOT_CHANGED',market_HTTP=0)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--index',required=True);p.add_argument('--index-sha256',required=True)
    p.add_argument('--job',required=True);p.add_argument('--archives',required=True);p.add_argument('--destination',required=True);a=p.parse_args()
    if file_sha(a.index)!=a.index_sha256:raise ValueError('native_index_external_anchor_mismatch')
    index=json.loads(Path(a.index).read_bytes());records=[r for r in index['results'] if r['job']==a.job]
    if len(records)!=1 or records[0]['status']!='PASS_OFFLINE_REPROCESSING':raise ValueError('native_job_not_uniquely_verified')
    r=records[0]
    if Path(r['archive']).name!=r['archive']:raise ValueError('unsafe_native_archive_name')
    print(json.dumps(restore(Path(a.archives)/r['archive'],r,a.destination),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
