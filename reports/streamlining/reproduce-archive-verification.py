"""Read-only byte verification of the cold original-observation inventory."""
import collections,hashlib,json,sys,tarfile
from pathlib import Path

def verify(root=Path('.')):
    inventory=json.loads((root/'audit-archive/inventory.json').read_text());groups=collections.defaultdict(dict)
    for row in inventory['files']:
        if row['path'] in groups[row['archive']]:raise ValueError('duplicate original path')
        groups[row['archive']][row['path']]=row
    count=total=0
    for archive,expected in groups.items():
        seen=set()
        with tarfile.open(root/archive,'r:xz') as t:
            for member in t:
                if not member.isfile():continue
                if member.name not in expected or member.name in seen:raise ValueError('unexpected/duplicate archive member')
                raw=t.extractfile(member).read();record=expected[member.name]
                if hashlib.sha256(raw).hexdigest()!=record['sha256'] or len(raw)!=record['bytes']:raise ValueError('original bytes changed')
                count+=1;total+=len(raw);seen.add(member.name)
        if seen!=set(expected):raise ValueError('original observation missing')
    return {'archives':len(groups),'original_files_verified':count,'original_bytes_verified':total,'all_hashes_match':True,'restored_or_modified_files':0}
if __name__=='__main__':
    result=verify();out=Path(sys.argv[1] if len(sys.argv)>1 else '/tmp/archive-verification.json');out.write_text(json.dumps(result,indent=2)+'\n');print(result)
