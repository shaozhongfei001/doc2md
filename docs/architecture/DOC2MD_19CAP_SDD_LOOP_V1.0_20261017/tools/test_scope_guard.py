'''Self-test of the package file-scope guard in a throwaway Git repo. No project source edited.'''
from pathlib import Path
import json, subprocess, sys, tempfile

PACKAGE=Path(__file__).resolve().parents[1]
def main():
    results=[]
    with tempfile.TemporaryDirectory(prefix='doc2md-guard-check-') as temp:
        repo=Path(temp)
        def git(args):return subprocess.check_output(['git']+args,cwd=repo,stderr=subprocess.DEVNULL).decode().strip()
        git(['init','-q'])
        mainfile=repo/'backend/app/main.py';mainfile.parent.mkdir(parents=True);mainfile.write_text('BASE\n')
        index=repo/'specs/SPEC_INDEX.md';index.parent.mkdir(parents=True);index.write_text('BASE INDEX\n')
        git(['add','.'])
        git(['-c','user.name=ContractGuardFixture','-c','user.email=fixture@example.invalid','commit','-q','-m','synthetic guard fixture'])
        base=git(['rev-parse','HEAD'])
        def run(name,loop,want):
            r=subprocess.run([sys.executable,str(PACKAGE/'tools/check_loop_diff.py'),'--repo',str(repo),'--loop',loop,'--base-commit',base],text=True,capture_output=True)
            obj=json.loads(r.stdout)
            ok=obj['passed']==want and (r.returncode==0)==want
            results.append({'name':name,'expected_allowed':want,'passed':ok,'guard_result':obj})
        new=repo/'backend/app/extensions/io/probe.py';new.parent.mkdir(parents=True);new.write_text('# synthetic fixture\n')
        run('L01 permits new IO namespace','L01',True)
        mainfile.write_text('ALTERED\n')
        run('L01 rejects legacy application mutation','L01',False)
        mainfile.write_text('BASE\n');new.unlink()
        index.write_text('BASE INDEX\n\nDOC2MD-19CAP candidate selection\n')
        run('L00 permits scoped selection append','L00',True)
        index.write_text('REWRITTEN INDEX\nDOC2MD-19CAP\n')
        run('L00 rejects replacement of previous index text','L00',False)
    report={'check':'SCOPE_GUARD_SELF_TEST_SYNTHETIC','passed':all(x['passed'] for x in results),'results':results,'product_tests_executed':False}
    print(json.dumps(report,ensure_ascii=False,indent=2));return 0 if report['passed'] else 1
if __name__=='__main__':sys.exit(main())
