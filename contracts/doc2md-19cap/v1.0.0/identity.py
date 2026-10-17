'''Pure reference functions for new IDs. Existing doc2md IDs must not use this module.'''
import hashlib, json, math

def canonical_json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')

def digest(domain,payload):
    return hashlib.sha256(canonical_json({'domain':domain,'payload':payload})).hexdigest()

def source_version(payload):
    return 'sv-'+digest('source-version/1',payload)

def job_key(payload):
    return digest('enhancement-job/1',payload)

def generation(payload):
    return digest('enhancement-generation/1',payload)

def object_id(kind,generation_id,source_locator,local_path):
    return kind+'-'+digest('enhancement-object/1',{'generation_id':generation_id,'kind':kind,'source_locator':source_locator,'local_path':local_path})

def candidate_evidence_id(source_version_id,generation_id,locator,observation_class):
    return 'ce-'+digest('candidate-evidence/1',{'source_version_id':source_version_id,'generation_id':generation_id,'locator':locator,'observation_class':observation_class})
