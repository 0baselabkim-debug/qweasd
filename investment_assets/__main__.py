"""Explicit local-only CLI. No background service, Actions hook, or deployment."""
import argparse
import json
import duckdb
from . import Archive, ArchiveError
from . import sources


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',required=True,help='Explicit persistent archive directory outside every source checkout')
    commands=p.add_subparsers(dest='command',required=True)
    for name in ('ingest-publication','ingest-analysis','register-kis-receipt','ingest-kis-artifact'):
        sub=commands.add_parser(name);sub.add_argument('--repo',required=True);sub.add_argument('--commit',required=True);sub.add_argument('--branch',required=True)
        if 'kis' in name:sub.add_argument('--receipt-path',default='reports/kis-manual-20261026/40326538555.json')
        if name=='ingest-kis-artifact':sub.add_argument('--zip',required=True)
    commands.add_parser('verify');commands.add_parser('rebuild-catalog');commands.add_parser('list')
    sub=commands.add_parser('query');sub.add_argument('--channel',required=True,choices=sources.CHANNELS);sub.add_argument('--at',required=True)
    sub.add_argument('--knowledge-at');sub.add_argument('--generation');sub.add_argument('--asof');sub.add_argument('--kind');sub.add_argument('--entity');sub.add_argument('--limit',type=int,default=20);sub.add_argument('--reproducible-only',action='store_true')
    sub=commands.add_parser('restore-generation');sub.add_argument('--generation',required=True);sub.add_argument('--destination',required=True)
    sub=commands.add_parser('backup');sub.add_argument('--destination',required=True)
    sub=commands.add_parser('restore-backup');sub.add_argument('--archive',required=True);sub.add_argument('--sha256',required=True)
    a=p.parse_args(argv)
    try:
        if a.command=='restore-backup':result=Archive.restore_backup(a.archive,a.root,a.sha256)
        else:
            # Verify source bytes before creating an archive whenever possible.
            if a.command.startswith('ingest-') or a.command=='register-kis-receipt':
                src=sources.GitSource(a.repo,a.commit,a.branch)
                if a.command=='ingest-publication':bundle=sources.publication(src)
                elif a.command=='ingest-analysis':bundle=sources.analysis(src)
                elif a.command=='ingest-kis-artifact':bundle=sources.kis_artifact(src,a.receipt_path,a.zip)
                else:bundle=sources.kis_receipt(src,a.receipt_path)[0]
                result=Archive(a.root).ingest(bundle)
            else:
                store=Archive(a.root)
                if a.command=='verify':result=store.verify()
                elif a.command=='rebuild-catalog':result=store.rebuild_catalog()
                elif a.command=='list':result=[store.verify_generation(p.name) for p in sorted((store.root/'generations').iterdir())]
                elif a.command=='query':result=store.query(**{k:getattr(a,k) for k in ('channel','at','knowledge_at','generation','kind','entity','asof','limit','reproducible_only')})
                elif a.command=='restore-generation':result=store.restore_generation(a.generation,a.destination)
                else:result=store.backup(a.destination)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 1 if isinstance(result,dict) and result.get('status') in ('FAIL','INCOMPLETE') else 0
    except (ArchiveError,OSError,KeyError,ValueError,duckdb.Error) as exc:
        print(json.dumps(dict(status='FAIL',reason=str(exc) if isinstance(exc,ArchiveError) else type(exc).__name__,operating_writes_requested=False),ensure_ascii=False))
        return 1


if __name__=='__main__':raise SystemExit(main())
