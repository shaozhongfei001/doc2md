'''Author self-check of reference contracts and package integrity. NOT independent product QA.'''
from pathlib import Path
import copy, hashlib, importlib.util, json, sys, re
sys.dont_write_bytecode=True

ROOT=Path(__file__).resolve().parents[1]
def load_module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m
m=load_module('contract_models',ROOT/'contracts/contract_models.py')
ident=load_module('contract_identity',ROOT/'contracts/identity.py')
results=[]
def check(name,fn):
    try:fn();results.append({'name':name,'result':'PASS'})
    except Exception as e:results.append({'name':name,'result':'FAIL','error':str(e)})
def expect(ok,message):
    if not ok:raise AssertionError(message)
def read(p):return json.loads((ROOT/p).read_text())
examples=read('contracts/example_map.json')
for name,paths in examples.items():
    def positive(name=name,paths=paths):
        val=read('contracts/'+paths['example'])
        m.ROOT_MODELS[name].model_validate_json(json.dumps(val,ensure_ascii=False))
    check('example:'+name,positive)
    def schema(name=name,paths=paths):
        supplied=read('contracts/'+paths['schema'])
        plain={k:v for k,v in supplied.items() if k not in ['$schema','$id','x-contract-state']}
        expect(plain==m.ROOT_MODELS[name].model_json_schema(),'schema/model drift')
    check('schema:'+name,schema)
base=read('contracts/examples/document-enhancement.example.json')
sv=read('contracts/examples/source-version.example.json')
negatives=[]
def add(name,model,payload):negatives.append((name,model,payload))
x=copy.deepcopy(sv);x['version_kind']='SOURCE_NATIVE';x['source_revision']=None
add('native-revision-missing',m.SourceVersion,x)
x=copy.deepcopy(sv);x['source_kind']='PEER_REFERENCE';x['license_ref']=None
add('external-license-missing',m.SourceVersion,x)
x=read('contracts/examples/source-event.example.json');x['source_version_id']=None
add('upsert-without-version',m.SourceEvent,x)
x=copy.deepcopy(base);x['tables'][0]['cells'][0]['bbox']=[0,0,1,1]
add('unexpected-cell-field',m.Enhancement,x)
x=copy.deepcopy(base);x['tables'][0]['cells'][0]['rowspan']=0
add('zero-span',m.Enhancement,x)
x=copy.deepcopy(base);x['tables'][0]['cells'][1]['row']=0
add('overlapping-span',m.Enhancement,x)
x=copy.deepcopy(base);x['tables'][0]['cells'].pop();x['tables'][0]['slots'][2][1]=None
add('unexplained-hole',m.Enhancement,x)
x=copy.deepcopy(base);x['tables'][0]['slots'][2][1]=x['tables'][0]['slots'][2][0]
add('incorrect-slot-map',m.Enhancement,x)
x=copy.deepcopy(base);x['evidence'][0]['locator']['source_sha256']='f'*64
add('wrong-evidence-source',m.Enhancement,x)
x=copy.deepcopy(base);x['evidence'][0]['review_state']='ACCEPTED'
add('accepted-without-review',m.Enhancement,x)
x=copy.deepcopy(base);x['evidence'][0]['locator']['view']='REVIEW_CONTEXT'
add('deleted-review-context-in-final',m.Enhancement,x)
x=read('contracts/examples/correction-patch.example.json')
x['operations'][0]['path']='/content_sha256';x['operations'][1]['path']='/content_sha256'
add('patch-protected-path',m.CorrectionPatch,x)
x=read('contracts/examples/correction-patch.example.json');x['operations'][0]['op']='replace'
add('patch-without-test',m.CorrectionPatch,x)
x=read('contracts/examples/enhancement-release.example.json');x['state']='ACTIVE';x['review_decision_refs']=['fake-review']
add('release-without-consumer-ack',m.ReleaseManifest,x)
x=read('contracts/examples/enhancement-dependencies.example.json')
x['edges'].append({'parent_object_id':x['nodes'][1]['object_id'],'child_object_id':x['nodes'][0]['object_id'],'relation':'DERIVED_FROM'})
add('processing-dependency-cycle',m.DependencyManifest,x)
x=copy.deepcopy(base)
x['geometries']=[{'geometry_id':'g','page_no':1,'original_asset':{'root':'EXTENSION','relative_path':'assets/a.png','sha256':'a'*64},'original_width_px':100,'original_height_px':100,'processed_width_px':100,'processed_height_px':100,'crop_origin_px':[0,0],'transform_original_to_processed':[0]*9,'unit_verified':True}]
add('singular-transform',m.Enhancement,x)
x=copy.deepcopy(base)
eid=x['evidence'][0]['candidate_evidence_id']
x['visuals']=[{'visual_id':'v','region_evidence_id':eid,'classification':'CHART','classification_state':'CANDIDATE','engine_confidence':None,'flow':None,'chart':{'axes':[],'points':[{'point_id':'p','series':None,'period':None,'axis_id':None,'unit_raw':None,'value_type':'ESTIMATED','exact_value':None,'estimated_value':'12.5','evidence_ids':[eid],'source_table_cell_id':None}],'original_data_ref':None}}]
add('numeric-estimate-default-disabled',m.Enhancement,x)
x=copy.deepcopy(base)
x['tables'][0]['cell_semantics'][0]['column_header_cell_ids']=[x['tables'][0]['cell_semantics'][0]['data_cell_id']]
add('data-cell-its-own-header',m.Enhancement,x)
x=read('contracts/examples/extension-manifest.example.json');x['files'][0]['file']['root']='RAW_VAULT'
add('new-output-writes-vault',m.ExtensionManifest,x)
add('path-traversal',m.FileRef,{'root':'EXTENSION','relative_path':'../secret','sha256':'a'*64})
add('estimate-not-exact',m.ChartPoint,{'point_id':'p','series':None,'period':None,'axis_id':None,'unit_raw':None,'value_type':'ESTIMATED','exact_value':'12.5','estimated_value':None,'evidence_ids':['ce'],'source_table_cell_id':None})
negative_guards={
 'native-revision-missing':'native version requires',
 'external-license-missing':'external source requires license',
 'upsert-without-version':'upsert/restore requires',
 'unexpected-cell-field':'Extra inputs are not permitted',
 'zero-span':'greater than 0',
 'overlapping-span':'overlapping span',
 'unexplained-hole':'omitted slots require evidence',
 'incorrect-slot-map':'slot-to-cell map mismatch',
 'wrong-evidence-source':'wrong source digest',
 'accepted-without-review':'version-bound review',
 'deleted-review-context-in-final':'review context',
 'patch-protected-path':'protected patch path',
 'patch-without-test':'each replace must follow same-path test',
 'release-without-consumer-ack':'acknowledgment missing',
 'processing-dependency-cycle':'dependency cycle',
 'singular-transform':'not invertible',
 'numeric-estimate-default-disabled':'numeric estimate disabled',
 'data-cell-its-own-header':'its own header',
 'new-output-writes-vault':'only be inside extension root',
 'path-traversal':'traversal-free',
 'estimate-not-exact':'cannot fill exact value'
}
for name,model,payload in negatives:
    def negative(model=model,payload=payload,name=name):
        failed=False
        try:model.model_validate_json(json.dumps(payload,ensure_ascii=False))
        except ValueError as exc:
            expect(negative_guards[name] in str(exc),'rejected for a different reason: '+str(exc))
            failed=True
        expect(failed,'invalid example was accepted')
    check('negative:'+name,negative)
def all_refs():
    trace=read('loop/requirements_trace.json');tests=read('loop/test_cases.json');lp=read('loop/loop_contract.json')
    expect(len(trace)==19,'requirements count')
    expect(len({x['requirement_id'] for x in trace})==19,'duplicate requirement')
    expect(len(lp['loops'])==9,'loop count')
    tmap={x['test_id']:x for x in tests};reqset={x['requirement_id'] for x in trace}
    expect(all(x['status']=='NOT_RUN' for x in tests),'product tests falsely executed')
    expect(len(lp['gates'])==6 and all(x['status']=='NOT_RUN' for x in lp['gates']),'product gates falsely executed')
    expect(all(x['status']=='PLANNED' for x in lp['loops']),'loops falsely executed')
    for r in trace:
        expect(len(r['test_ids'])>=3,'insufficient case split')
        expect(all(t in tmap and tmap[t]['requirement_id']==r['requirement_id'] for t in r['test_ids']),'TC references')
    expect({t['requirement_id'] for t in tests}==reqset,'untraced test/requirement')
    expect(set(lp['scope_requirement_ids'])==reqset,'scope drift')
    ids={x['loop_id'] for x in lp['loops']}
    for loop in lp['loops']:expect(set(loop['depends_on'])<=ids,'missing dependency')
    # Acyclic loop dependencies.
    graph={x['loop_id']:x['depends_on'] for x in lp['loops']}
    def walk(n,path):
        expect(n not in path,'loop dependency cycle')
        for k in graph[n]:walk(k,path+[n])
    for n in graph:walk(n,[])
check('traceability-and-unexecuted-statuses',all_refs)
def alignment():
    a=read('loop/decision_alignment.json')
    expect({r['id'] for r in a['decisions']}=={f'D-{i:02d}' for i in range(1,21)},'D01-D20 coverage')
    expect({r['id'] for r in a['redlines']}=={f'L-{i}' for i in range(1,8)},'L1-L7 coverage')
    changes=read('loop/change_decisions.json')
    expect(len(changes)==10,'change decisions count')
    expect(all('PROPOSED' in c['state'] or c['state']=='OWNER_DECISION_REQUIRED_FOR_NUMERIC_ESTIMATE' for c in changes),'fake decision approval')
check('decisions-redlines-and-change-status',alignment)
def identities():
    vec=read('contracts/identity_vectors.json')
    expect(ident.source_version(vec['source_version']['input'])==vec['source_version']['expected_id'],'source identity vector')
    expect(ident.job_key(vec['job']['input'])==vec['job']['expected_id'],'job identity vector')
    expect(ident.generation(vec['generation']['input'])==vec['generation']['expected_id'],'generation vector')
    expect(base['generation_id']==vec['generation']['expected_id'],'example generation vector mismatch')
check('new-identity-fixed-vectors',identities)
def old_uid_vector():
    # Frozen legacy source formula, no import/DB execution; a reference vector only.
    chunk='0123456789abcdef'*4
    legacy=hashlib.sha256(f'evidence|v1|{chunk}|2|19|section_quote'.encode()).hexdigest()
    vec=read('contracts/legacy_uid_vector.json')
    expect(legacy==vec['expected_evidence_uid'],'legacy vector mismatch')
check('legacy-uid-reference-vector',old_uid_vector)
def markdown():
    for p in ROOT.rglob('*.md'):
        text=p.read_text()
        expect(text.count(chr(96)*3)%2==0,f'unclosed fence: {p}')
check('markdown-fences',markdown)
def python_syntax():
    import ast
    for p in ROOT.rglob('*.py'):ast.parse(p.read_text())
check('python-reference-and-checker-syntax',python_syntax)
def whitelist_paths():
    lp=read('loop/loop_contract.json')
    expect('global_blacklist' not in lp,'ambiguous blacklist expression')
    for loop in lp['loops']:
        expect(all('{' not in p and '}' not in p and ' (' not in p for p in loop['allowed_new_or_scoped_paths']),'unexpanded machine path')
    expect(all(p.startswith('backend/app/extensions/') or not p.startswith('backend/app/') for loop in lp['loops'] for p in loop['allowed_new_or_scoped_paths']),'legacy application edit whitelisted')
check('machine-whitelist-paths',whitelist_paths)
def integrity():
    p=ROOT/'MANIFEST.json'
    expect(p.exists(),'MANIFEST.json is required')
    manifest=json.loads(p.read_text())
    listed={f['path'] for f in manifest['files']}
    actual={p.relative_to(ROOT).as_posix() for p in ROOT.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.name!='MANIFEST.json'}
    expect(listed==actual,'manifest does not enumerate all deliverable files')
    for f in manifest['files']:
        target=ROOT/f['path']
        expect(target.is_file(),f'missing {f["path"]}')
        expect(hashlib.sha256(target.read_bytes()).hexdigest()==f['sha256'],f'integrity {f["path"]}')
check('manifest-integrity',integrity)
report={'check_type':'AUTHOR_CONTRACT_PACKAGE_SELF_CHECK','product_tests_executed':False,'independent_qa_executed':False,
 'reference_model_engine':'Pydantic '+__import__('pydantic').__version__,
 'schema_examples':len(examples),'negative_examples':len(negatives),'requirements_designed':19,
 'product_test_cases_planned':len(read('loop/test_cases.json')),'loops_planned':9,
 'results':results,'passed':all(x['result']=='PASS' for x in results)}
print(json.dumps(report,ensure_ascii=False,indent=2))
sys.exit(0 if report['passed'] else 1)
