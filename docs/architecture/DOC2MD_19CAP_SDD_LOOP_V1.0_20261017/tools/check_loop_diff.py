'''Read-only Loop file-scope guard. Product behavior and runtime writes require separate gates.'''
from pathlib import Path
import argparse, fnmatch, hashlib, json, re, subprocess, sys

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--repo',required=True);ap.add_argument('--loop',required=True)
    ap.add_argument('--base-commit',required=True,help='Actual full 40-hex commit captured at this Loop entry')
    a=ap.parse_args();repo=Path(a.repo).resolve()
    if not re.fullmatch('[0-9a-f]{40}',a.base_commit):ap.error('base commit must be full lowercase hex')
    package=Path(__file__).resolve().parents[1]
    con=json.loads((package/'loop/loop_contract.json').read_text())
    lp=next((l for l in con['loops'] if l['loop_id']==a.loop),None)
    if lp is None:ap.error('unknown Loop')
    baseline=json.loads((package/'inputs/baseline_lock.json').read_text())
    protected={x['path']:x['sha256'] for x in baseline['files']}
    known_modes=set(baseline['known_mode_only_changes'])
    def git(args):return subprocess.check_output(['git']+args,cwd=repo)
    names=set(git(['diff','--name-only','--no-renames','-z',a.base_commit,'--']).decode().split(chr(0)))
    names|=set(git(['ls-files','--others','--exclude-standard','-z']).decode().split(chr(0)));names.discard('')
    errors=[];accepted=[];ignored_modes=[]
    for name in sorted(names):
        p=repo/name
        if name in known_modes and p.is_file() and name in protected and hashlib.sha256(p.read_bytes()).hexdigest()==protected[name]:
            ignored_modes.append(name);continue
        allowed=any(fnmatch.fnmatchcase(name,pat) for pat in lp['allowed_new_or_scoped_paths'])
        if not allowed:errors.append({'path':name,'reason':'OUTSIDE_LOOP_WHITELIST'});continue
        scopes=[x for x in lp['scoped_existing_edits'] if x['path']==name]
        if name in protected:
            if not scopes:errors.append({'path':name,'reason':'PROTECTED_EXISTING_FILE'});continue
            prior=git(['show',a.base_commit+':'+name])
            current=p.read_bytes() if p.is_file() else b''
            if not current.startswith(prior) or scopes[0]['required_marker'].encode() not in current[len(prior):]:
                errors.append({'path':name,'reason':'NOT_SCOPED_APPEND'});continue
        if any(name.startswith(r) for r in con['forbidden_write_roots']):
            errors.append({'path':name,'reason':'FORBIDDEN_WRITE_ROOT'});continue
        accepted.append(name)
    report={'check':'LOOP_DIFF_SCOPE_ONLY','loop':a.loop,'base_commit':a.base_commit,'passed':not errors,
      'allowed_changed_paths':accepted,'ignored_known_mode_only_changes':ignored_modes,'errors':errors,
      'limits':'No code correctness, tests, bank authorization, role identity or runtime side effects verified'}
    print(json.dumps(report,ensure_ascii=False,indent=2));return 0 if report['passed'] else 1
if __name__=='__main__':sys.exit(main())
