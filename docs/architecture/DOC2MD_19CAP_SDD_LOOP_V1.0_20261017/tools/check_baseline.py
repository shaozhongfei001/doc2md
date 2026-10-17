'''Read-only protected baseline verifier. Does not install, copy, accept revisions or modify repo.'''
from pathlib import Path
import argparse, hashlib, json, subprocess, sys

def run(repo,allow_index_append=False):
    root=Path(__file__).resolve().parents[1]
    lock=json.loads((root/'inputs/baseline_lock.json').read_text())
    repo=Path(repo).resolve(); errors=[]; exceptions=[]
    expected={r['path']:r['sha256'] for r in lock['files']}
    for name,want in expected.items():
        p=repo/name
        if not p.is_file():
            errors.append({'path':name,'error':'MISSING_PROTECTED_FILE'});continue
        got=hashlib.sha256(p.read_bytes()).hexdigest()
        if got==want:continue
        if name=='specs/SPEC_INDEX.md' and allow_index_append:
            try:old=subprocess.check_output(['git','show',lock['zip_head']+':'+name],cwd=repo)
            except subprocess.CalledProcessError:old=None
            current=p.read_bytes()
            if old is not None and current.startswith(old) and b'DOC2MD-19CAP' in current[len(old):]:
                exceptions.append({'path':name,'rule':'APPEND_ONLY_NEW_SELECTION'});continue
        errors.append({'path':name,'error':'PROTECTED_BYTES_CHANGED'})
    protected_prefixes=['backend/app/','backend/tests/','sql/','.cursor/rules/','specs/']
    allowed_new=['backend/app/extensions/','backend/tests/extensions/','specs/proposals/']
    for prefix in protected_prefixes:
        d=repo/prefix
        if not d.exists():continue
        for p in d.rglob('*'):
            if not p.is_file() or '__pycache__' in p.parts or p.suffix=='.pyc':continue
            rel=p.relative_to(repo).as_posix()
            if rel not in expected and not any(rel.startswith(a) for a in allowed_new):
                errors.append({'path':rel,'error':'UNEXPECTED_PROTECTED_ADDITION'})
    try:
        current_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
        ancestry=subprocess.run(['git','merge-base','--is-ancestor',lock['zip_head'],'HEAD'],cwd=repo,capture_output=True)
        if ancestry.returncode!=0:errors.append({'path':'.git','error':'BASELINE_HEAD_NOT_ANCESTOR_OR_SHALLOW_HISTORY_MISSING'})
    except Exception as exc:
        current_head=None;errors.append({'path':'.git','error':'HEAD_NOT_VERIFIED','type':type(exc).__name__})
    return {'check':'PROTECTED_BASELINE_CONTENT_ONLY','passed':not errors,'current_head':current_head,
      'checked_file_count':len(expected),'mode_changes_not_treated_as_content_changes':lock['known_mode_only_changes'],
      'append_exceptions':exceptions,'errors':errors,
      'limits':'No product tests, engine calls, bank authorization or independent QA performed'}
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--repo',required=True);ap.add_argument('--allow-index-append',action='store_true')
    a=ap.parse_args();r=run(a.repo,a.allow_index_append)
    print(json.dumps(r,ensure_ascii=False,indent=2));sys.exit(0 if r['passed'] else 1)
