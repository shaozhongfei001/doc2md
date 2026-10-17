'''Design reference models only: no OCR, connector, database or production service implementation.
Pydantic 2.x; runtime target remains repository-approved version. Schema checks are necessary but
not sufficient for engine quality, permissions, identity, durability or project acceptance.
'''
from __future__ import annotations
from typing import Annotated, Literal
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, JsonValue, AwareDatetime, model_validator

Hash = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
ID = Annotated[str, Field(min_length=1)]
DecimalText = Annotated[str, Field(pattern=r'^-?(0|[1-9][0-9]*)(\.[0-9]+)?$')]
Nonempty = Annotated[str, Field(min_length=1)]
class Closed(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
class FileRef(Closed):
    root: Literal['EXTENSION','RAW_VAULT','PARSED']
    relative_path: Nonempty
    sha256: Hash
    @model_validator(mode='after')
    def safe(self):
        p=self.relative_path
        if p.startswith(('/', '\\')) or '\\' in p or ':' in p or '..' in p.split('/'):
            raise ValueError('file reference must be root-relative and traversal-free')
        return self
class SourceVersion(Closed):
    schema_version: Literal['source-version/1.0']
    source_version_id: ID
    source_id: ID
    external_object_id: ID
    version_kind: Literal['SOURCE_NATIVE','LOCAL_SNAPSHOT']
    source_revision: str | None
    snapshot_id: str | None
    content_sha256: Hash | None
    legacy_query_ref: str | None
    processing_route: Literal['REUSE_QUERY','REPROCESS','METADATA_ONLY']
    source_kind: Literal['BANK_RULE','EXTERNAL_POLICY','PEER_REFERENCE','VISIT_RECORD','OTHER']
    authority_class: Literal['BANK_EFFECTIVE_RULE','EXTERNAL_REFERENCE','RESTRICTED_HISTORY','UNCLASSIFIED']
    permission_ref: ID
    owner_ref: ID
    classification: Literal['PUBLIC','INTERNAL','CONFIDENTIAL','RESTRICTED']
    purpose_scopes: list[Nonempty]
    license_ref: str | None
    valid_from: AwareDatetime | None
    valid_to: AwareDatetime | None
    source_file_ref: FileRef | None
    @model_validator(mode='after')
    def consistent(self):
        if self.version_kind=='SOURCE_NATIVE' and not self.source_revision:
            raise ValueError('native version requires actual source revision')
        if self.version_kind=='LOCAL_SNAPSHOT' and (not self.snapshot_id or self.source_revision is not None):
            raise ValueError('snapshot is not a source revision')
        if self.processing_route=='REPROCESS' and (not self.content_sha256 or self.source_file_ref is None):
            raise ValueError('reprocessing requires source bytes and digest')
        if self.source_file_ref is not None and self.content_sha256!=self.source_file_ref.sha256:
            raise ValueError('source file digest mismatch')
        if self.processing_route=='REUSE_QUERY' and not self.legacy_query_ref:
            raise ValueError('reuse route needs authorized query reference')
        if self.source_kind in ['EXTERNAL_POLICY','PEER_REFERENCE'] and not self.license_ref:
            raise ValueError('external source requires license reference')
        if self.valid_from and self.valid_to and self.valid_to<=self.valid_from:
            raise ValueError('invalid validity window')
        if not self.purpose_scopes:raise ValueError('purpose required')
        return self
class SourceEvent(Closed):
    schema_version: Literal['source-event/1.0']
    event_id: ID
    source_id: ID
    external_object_id: ID
    event_type: Literal['UPSERT','REVOKE','DELETE','RESTORE']
    source_version_id: ID | None
    source_order_token: str | None
    source_epoch: int = Field(ge=0)
    access_revision: ID
    payload_hash: Hash
    observed_at: AwareDatetime
    @model_validator(mode='after')
    def event(self):
        if self.event_type in ['UPSERT','RESTORE'] and self.source_version_id is None:
            raise ValueError('upsert/restore requires a source version')
        return self
class SyncCheckpoint(Closed):
    schema_version: Literal['source-checkpoint/1.0']
    source_id: ID
    generation_id: ID
    previous_generation_id: ID | None
    cursor_token: str | None
    cursor_sha256: Hash | None
    committed_event_ids: list[ID]
    enumeration_complete: bool
    selection_scope_hash: Hash
    authorization_snapshot_ref: ID
class PageGeometry(Closed):
    geometry_id: ID
    page_no: int = Field(ge=1)
    original_asset: FileRef
    original_width_px: int = Field(gt=0)
    original_height_px: int = Field(gt=0)
    processed_width_px: int = Field(gt=0)
    processed_height_px: int = Field(gt=0)
    crop_origin_px: tuple[FiniteFloat,FiniteFloat]
    transform_original_to_processed: list[FiniteFloat] | None
    unit_verified: bool
    @model_validator(mode='after')
    def matrix(self):
        a=self.transform_original_to_processed
        if a is not None:
            if len(a)!=9:raise ValueError('homogeneous matrix must have 9 entries')
            d=a[0]*(a[4]*a[8]-a[5]*a[7])-a[1]*(a[3]*a[8]-a[5]*a[6])+a[2]*(a[3]*a[7]-a[4]*a[6])
            if abs(d)<1e-12:raise ValueError('transform is not invertible')
        return self
class DocxLocator(Closed):
    kind: Literal['DOCX_XML']
    source_sha256: Hash
    part_name: Literal['word/document.xml','word/footnotes.xml','word/endnotes.xml','word/comments.xml']
    part_sha256: Hash
    element_path: Nonempty
    view: Literal['FINAL','REVIEW_CONTEXT']
class PageLocator(Closed):
    kind: Literal['PAGE_REGION']
    source_sha256: Hash
    geometry_id: ID
    page_no: int = Field(ge=1)
    level: Literal['REGION_VERIFIED','PAGE_ONLY']
    normalized_polygon: list[tuple[FiniteFloat,FiniteFloat]] | None
    @model_validator(mode='after')
    def polygon(self):
        poly=self.normalized_polygon
        if self.level=='REGION_VERIFIED' and (poly is None or len(poly)<3):
            raise ValueError('verified region requires polygon')
        if self.level=='PAGE_ONLY' and poly is not None:
            raise ValueError('page-only evidence must not invent a region')
        if poly:
            if not all(0<=a<=1 and 0<=b<=1 for a,b in poly):raise ValueError('normalized polygon outside page')
            area=abs(sum(poly[i][0]*poly[(i+1)%len(poly)][1]-poly[(i+1)%len(poly)][0]*poly[i][1] for i in range(len(poly)))/2)
            if area<=0:raise ValueError('polygon area must be positive')
        return self
class TextLocator(Closed):
    kind: Literal['TEXT_SPAN']
    source_sha256: Hash
    text_file: FileRef
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    @model_validator(mode='after')
    def offsets(self):
        if self.end_offset<=self.start_offset:raise ValueError('invalid half-open span')
        return self
Locator=Annotated[DocxLocator | PageLocator | TextLocator, Field(discriminator='kind')]
class CandidateEvidence(Closed):
    candidate_evidence_id: ID
    source_version_id: ID
    locator: Locator
    raw_engine_ref: FileRef | None
    quote: str | None
    crop_ref: FileRef | None
    observation_class: Literal['SOURCE_CONTENT','MODEL_INTERPRETATION','HUMAN_TRANSCRIPTION']
    review_state: Literal['UNREVIEWED','ACCEPTED','REJECTED','STALE']
    review_decision_ref: ID | None
    @model_validator(mode='after')
    def review(self):
        if self.review_state=='ACCEPTED' and not self.review_decision_ref:
            raise ValueError('accepted candidate needs version-bound review')
        return self
class Cell(Closed):
    cell_id: ID
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    rowspan: int = Field(gt=0)
    colspan: int = Field(gt=0)
    text: str | None
    text_origin: Literal['SOURCE_XML','ENGINE_OCR','HUMAN_PATCH']
    text_alignment: Literal['NATIVE','ENGINE_CELL_TEXT','GEOMETRIC_CANDIDATE','HUMAN_CONFIRMED','UNKNOWN']
    engine_confidence: FiniteFloat | None
    token_ids: list[ID]
    evidence_ids: list[ID]
    nested_table_ids: list[ID]
    @model_validator(mode='after')
    def confidence(self):
        if self.engine_confidence is not None and not 0<=self.engine_confidence<=1:
            raise ValueError('engine confidence outside [0,1]')
        return self
class CellSemantics(Closed):
    data_cell_id: ID
    column_header_cell_ids: list[ID]
    row_header_cell_ids: list[ID]
    note_ids: list[ID]
    unit_raw: str | None
    state: Literal['CANDIDATE','CONFIRMED','NEEDS_REVIEW']
    review_decision_ref: ID | None = None
    @model_validator(mode='after')
    def confirmed(self):
        if self.state=='CONFIRMED' and not self.review_decision_ref:raise ValueError('confirmed header needs review')
        return self
class Note(Closed):
    note_id: ID
    text: Nonempty
    scope: Literal['TABLE','COLUMN','ROW','CELL','UNRESOLVED']
    target_ids: list[ID]
    evidence_ids: list[ID]
    state: Literal['CANDIDATE','CONFIRMED','NEEDS_REVIEW']
    review_decision_ref: ID | None = None
    @model_validator(mode='after')
    def scope_targets(self):
        if self.state=='CONFIRMED' and not self.review_decision_ref:raise ValueError('confirmed note needs review')
        if self.scope=='UNRESOLVED' and (self.target_ids or self.state!='NEEDS_REVIEW'):
            raise ValueError('unresolved scope cannot invent targets')
        if self.scope!='UNRESOLVED' and not self.target_ids:
            raise ValueError('resolved scope needs targets')
        return self
class Table(Closed):
    table_id: ID
    ir_ref: str | None
    parent_cell_id: ID | None
    n_rows: int = Field(ge=0)
    n_cols: int = Field(ge=0)
    cells: list[Cell]
    slots: list[list[ID | None]]
    omitted_slots: list[tuple[int,int]]
    omission_evidence_ids: list[ID]
    cell_semantics: list[CellSemantics]
    notes: list[Note]
    source_html_ref: FileRef | None
    structure_state: Literal['VALID_CANDIDATE','CONFIRMED']
    review_decision_ref: ID | None = None
    @model_validator(mode='after')
    def grid(self):
        if self.structure_state=='CONFIRMED' and not self.review_decision_ref:raise ValueError('confirmed table needs review')
        if len(self.slots)!=self.n_rows or any(len(row)!=self.n_cols for row in self.slots):
            raise ValueError('slot matrix shape mismatch')
        ids=[c.cell_id for c in self.cells]
        if len(ids)!=len(set(ids)):raise ValueError('duplicate cell IDs')
        expected=[[None for _ in range(self.n_cols)] for _ in range(self.n_rows)]
        for c in self.cells:
            if c.row+c.rowspan>self.n_rows or c.col+c.colspan>self.n_cols:
                raise ValueError('cell span outside grid')
            for r in range(c.row,c.row+c.rowspan):
                for col in range(c.col,c.col+c.colspan):
                    if expected[r][col] is not None:raise ValueError('overlapping span')
                    expected[r][col]=c.cell_id
        omitted=set(self.omitted_slots)
        actual={(r,c) for r,row in enumerate(expected) for c,v in enumerate(row) if v is None}
        if actual and not self.omission_evidence_ids:raise ValueError('omitted slots require evidence')
        if omitted!=actual:raise ValueError('unexplained holes; omitted slots require source proof')
        if expected!=self.slots:raise ValueError('slot-to-cell map mismatch')
        for s in self.cell_semantics:
            if s.data_cell_id in s.column_header_cell_ids+s.row_header_cell_ids:raise ValueError('data cell cannot be its own header')
            if s.data_cell_id not in ids or not set(s.column_header_cell_ids+s.row_header_cell_ids)<=set(ids):
                raise ValueError('dangling header reference')
        return self
class Continuation(Closed):
    relation_id: ID
    previous_table_id: ID
    next_table_id: ID
    decision: Literal['JOIN_CANDIDATE','SEPARATE','NEEDS_REVIEW','CONFIRMED_JOIN']
    compared_dimensions: list[str]
    evidence_ids: list[ID]
    review_decision_ref: ID | None
class FlowNode(Closed):
    node_id: ID
    text: str | None
    role: Literal['START','END','ACTION','DECISION','UNKNOWN']
    swimlane: str | None
    evidence_ids: list[ID]
class FlowEdge(Closed):
    edge_id: ID
    source_node_id: ID | None
    target_node_id: ID | None
    label: str | None
    direction_state: Literal['CANDIDATE','CONFIRMED','UNRESOLVED']
    evidence_ids: list[ID]
    review_decision_ref: ID | None = None
class FlowGraph(Closed):
    nodes: list[FlowNode]
    edges: list[FlowEdge]
    @model_validator(mode='after')
    def graph(self):
        ids=[x.node_id for x in self.nodes]
        if len(ids)!=len(set(ids)):raise ValueError('duplicate node ID')
        for edge in self.edges:
            if edge.source_node_id is not None and edge.source_node_id not in ids:raise ValueError('missing source node')
            if edge.target_node_id is not None and edge.target_node_id not in ids:raise ValueError('missing target node')
            if edge.direction_state=='CONFIRMED' and not edge.review_decision_ref:raise ValueError('confirmed edge needs review')
            if edge.direction_state=='CONFIRMED' and (edge.source_node_id is None or edge.target_node_id is None):
                raise ValueError('confirmed edge needs endpoints')
        return self
class Axis(Closed):
    axis_id: ID
    label: str | None
    unit_raw: str | None
    scale_type: Literal['LINEAR','LOG','CATEGORY','UNKNOWN']
    tick_labels: list[str]
    evidence_ids: list[ID]
class ChartPoint(Closed):
    point_id: ID
    series: str | None
    period: str | None
    axis_id: ID | None
    unit_raw: str | None
    value_type: Literal['PRINTED_LABEL','SOURCE_TABLE','ESTIMATED','TREND_ONLY','UNKNOWN']
    exact_value: DecimalText | None
    estimated_value: DecimalText | None
    evidence_ids: list[ID]
    source_table_cell_id: ID | None
    @model_validator(mode='after')
    def values(self):
        if self.value_type in ['ESTIMATED','TREND_ONLY','UNKNOWN'] and self.exact_value is not None:
            raise ValueError('estimate/trend/unknown cannot fill exact value')
        if self.value_type!='ESTIMATED' and self.estimated_value is not None:
            raise ValueError('numeric estimate requires estimate type')
        if self.value_type=='SOURCE_TABLE' and not self.source_table_cell_id:
            raise ValueError('table-derived point needs exact cell reference')
        if self.value_type in ['PRINTED_LABEL','SOURCE_TABLE'] and self.exact_value is None:
            raise ValueError('exact reading requires a value')
        return self
class Chart(Closed):
    axes: list[Axis]
    points: list[ChartPoint]
    original_data_ref: ID | None
    @model_validator(mode='after')
    def axes_exist(self):
        ids={x.axis_id for x in self.axes}
        if any(p.axis_id is not None and p.axis_id not in ids for p in self.points):
            raise ValueError('unknown chart axis')
        return self
class Visual(Closed):
    visual_id: ID
    region_evidence_id: ID
    classification: Literal['TABLE','FLOW','CHART','DECORATIVE','UNKNOWN']
    classification_state: Literal['CANDIDATE','CONFIRMED','NEEDS_REVIEW']
    engine_confidence: FiniteFloat | None
    flow: FlowGraph | None
    chart: Chart | None
    review_decision_ref: ID | None = None
    @model_validator(mode='after')
    def branch(self):
        if self.classification_state=='CONFIRMED' and not self.review_decision_ref:raise ValueError('confirmed visual type needs review')
        if self.classification!='FLOW' and self.flow is not None:raise ValueError('flow result in other branch')
        if self.classification!='CHART' and self.chart is not None:raise ValueError('chart result in other branch')
        return self
class Issue(Closed):
    code: Nonempty
    severity: Literal['BLOCKER','MAJOR','MINOR','NOTE']
    object_ids: list[ID]
    message: Nonempty
    raw_ref: FileRef | None
class Enhancement(Closed):
    schema_version: Literal['document-enhancement/1.0']
    generation_id: Hash
    job_key: Hash
    content_sha256: Hash
    source_version_ids: list[ID]
    base_manifest_sha256: Hash | None
    base_ir_sha256: Hash | None
    raw_output_sha256: Hash
    normalizer_version: Nonempty
    profile_sha256: Hash
    geometries: list[PageGeometry]
    tables: list[Table]
    continuations: list[Continuation]
    visuals: list[Visual]
    evidence: list[CandidateEvidence]
    issues: list[Issue]
    numeric_estimates_enabled: bool
    @model_validator(mode='after')
    def links(self):
        es=[e.candidate_evidence_id for e in self.evidence]
        if len(es)!=len(set(es)):raise ValueError('duplicate evidence ID')
        eset=set(es)
        ts={t.table_id for t in self.tables}
        geoms={g.geometry_id:g for g in self.geometries}
        cells={c.cell_id for t in self.tables for c in t.cells}
        for t in self.tables:
            if t.parent_cell_id is not None and t.parent_cell_id not in cells:raise ValueError('missing parent cell')
            if not set(t.omission_evidence_ids)<=eset:raise ValueError('omission evidence missing')
            for c in t.cells:
                if not set(c.evidence_ids)<=eset:raise ValueError('cell evidence not found')
                if not set(c.nested_table_ids)<=ts:raise ValueError('nested table missing')
            for n in t.notes:
                if not set(n.evidence_ids)<=eset:raise ValueError('note evidence missing')
            note_ids={n.note_id for n in t.notes}
            if any(not set(s.note_ids)<=note_ids for s in t.cell_semantics):raise ValueError('note reference missing')
        for r in self.continuations:
            if r.previous_table_id not in ts or r.next_table_id not in ts:raise ValueError('continuation endpoint missing')
            if r.decision=='CONFIRMED_JOIN' and not r.review_decision_ref:raise ValueError('join needs review')
        for e in self.evidence:
            if isinstance(e.locator,DocxLocator) and e.locator.view!='FINAL':raise ValueError('review context is not effective document evidence')
            if e.source_version_id not in self.source_version_ids:raise ValueError('source version missing')
            if e.locator.source_sha256!=self.content_sha256:raise ValueError('wrong source digest')
            if isinstance(e.locator,PageLocator):
                g=geoms.get(e.locator.geometry_id)
                if not g or e.locator.page_no!=g.page_no:raise ValueError('geometry link missing')
                if e.locator.level=='REGION_VERIFIED' and not g.unit_verified:raise ValueError('unverified geometry unit')
        for v in self.visuals:
            if v.region_evidence_id not in eset:raise ValueError('visual evidence missing')
            if v.flow:
                for x in v.flow.nodes+v.flow.edges:
                    if not x.evidence_ids or not set(x.evidence_ids)<=eset:raise ValueError('flow item evidence missing')
            if v.chart:
                for point in v.chart.points:
                    if not point.evidence_ids or not set(point.evidence_ids)<=eset:raise ValueError('point evidence missing')
                    if point.estimated_value is not None and not self.numeric_estimates_enabled:raise ValueError('numeric estimate disabled')
        return self
class PatchOperation(Closed):
    op: Literal['test','replace']
    path: Nonempty
    value: JsonValue
class CorrectionPatch(Closed):
    schema_version: Literal['correction-patch/1.0']
    patch_id: ID
    base_generation_id: Hash
    expected_current_generation_id: Hash
    target_object_ids: list[ID]
    operations: list[PatchOperation]
    reason: Nonempty
    evidence_ids: list[ID]
    review_ref: ID | None
    @model_validator(mode='after')
    def pairs(self):
        if not self.operations or len(self.operations)%2:raise ValueError('patch requires test/replace pairs')
        for i in range(0,len(self.operations),2):
            a,b=self.operations[i:i+2]
            if a.op!='test' or b.op!='replace' or a.path!=b.path:raise ValueError('each replace must follow same-path test')
            if not a.path.startswith(('/tables/','/visuals/','/continuations/')):
                raise ValueError('protected patch path')
            if any(x in a.path for x in ['/source_sha256','/content_sha256','/evidence/','/schema_version','/generation_id']):
                raise ValueError('identity/evidence cannot be overwritten by patch')
        return self
class ConsumerEvent(Closed):
    schema_version: Literal['consumer-event/1.0']
    event_id: ID
    event_type: Literal['INVALIDATE','REBUILD_READY','SOURCE_REVOKED']
    consumer_id: ID
    previous_generation_id: Hash | None
    target_generation_id: Hash | None
    object_ids: list[ID]
    source_access_epoch: int = Field(ge=0)
    idempotency_key: Hash
    state: Literal['PENDING','ACKNOWLEDGED','FAILED','NOT_CONNECTED']
    ack_ref: str | None
    @model_validator(mode='after')
    def ack(self):
        if self.state=='ACKNOWLEDGED' and not self.ack_ref:raise ValueError('acknowledged event needs actual receipt reference')
        return self
class ReleaseManifest(Closed):
    schema_version: Literal['enhancement-release/1.0']
    release_id: ID
    generation_ids: list[Hash]
    expected_previous_release_id: ID | None
    source_access_revisions: dict[str,int]
    required_consumer_ids: list[ID]
    review_decision_refs: list[ID]
    reconcile_report: FileRef
    state: Literal['PREPARED','LOCAL_READY','ACTIVE','RETIRED','BLOCKED']
    external_ack_refs: dict[str,str]
    @model_validator(mode='after')
    def ready(self):
        if self.state=='ACTIVE' and not set(self.required_consumer_ids)<=set(self.external_ack_refs):
            raise ValueError('required consumer acknowledgment missing')
        if self.state in ['LOCAL_READY','ACTIVE'] and not self.review_decision_refs:
            raise ValueError('release requires bound reviews')
        return self
class SourceAccessState(Closed):
    schema_version: Literal['source-access/1.0']
    source_id: ID
    external_object_id: ID
    access_epoch: int = Field(ge=0)
    state: Literal['ACTIVE','REVOKED','EXPIRED','QUARANTINED']
    current_source_version_id: ID | None
    permission_ref: ID
    purpose_scopes: list[Nonempty]
    authority_decision_ref: ID | None
    last_deny_event_id: ID | None
    lease_expires_at: AwareDatetime | None
    observed_at: AwareDatetime
    @model_validator(mode='after')
    def access(self):
        if self.state=='ACTIVE' and not self.current_source_version_id:raise ValueError('active source needs current version')
        return self
class ReviewDecision(Closed):
    schema_version: Literal['enhancement-review/1.0']
    review_id: ID
    object_type: Literal['PATCH','CANDIDATE','TABLE','FLOW','CHART','RELEASE']
    object_id: ID
    object_hash: Hash
    base_generation_id: Hash
    source_access_revisions: dict[str,int]
    scope_paths: list[Nonempty]
    decision: Literal['APPROVE','RETURN','REJECT']
    reason: Nonempty
    evidence_ids: list[ID]
    reviewer_subject: ID
    identity_mode: Literal['LOCAL_OS','BANK_AUTH']
    authority_ref: ID
    decided_at: AwareDatetime
class ArtifactFile(Closed):
    file: FileRef
    artifact_type: Literal['ENHANCEMENT','RAW','ASSET','RENDERED','REVIEW_CONTEXT','DEPENDENCIES','INPUT_SNAPSHOT']
class ExtensionManifest(Closed):
    schema_version: Literal['extension-manifest/1.0']
    job_key: Hash
    generation_id: Hash
    input_manifest: list[FileRef]
    producer_locks: list[FileRef]
    files: list[ArtifactFile]
    dependency_manifest_sha256: Hash
    execution_state: Literal['VALIDATED']
    created_at: AwareDatetime
    @model_validator(mode='after')
    def files_safe(self):
        keys=[(x.file.root,x.file.relative_path) for x in self.files]
        if len(keys)!=len(set(keys)):raise ValueError('duplicate artifact path')
        if any(x.file.root!='EXTENSION' for x in self.files):raise ValueError('new outputs may only be inside extension root')
        return self
class DependencyNode(Closed):
    object_id: ID
    generation_id: Hash
    object_type: Nonempty
    consumer_id: ID
    external: bool
class DependencyEdge(Closed):
    parent_object_id: ID
    child_object_id: ID
    relation: Literal['DERIVED_FROM','HEADER_CONTEXT','NOTE_CONTEXT','COMPOSITE_VIEW','EXPORTED_TO']
class DependencyManifest(Closed):
    schema_version: Literal['enhancement-dependencies/1.0']
    generation_id: Hash
    nodes: list[DependencyNode]
    edges: list[DependencyEdge]
    required_consumer_ids: list[ID]
    @model_validator(mode='after')
    def dag(self):
        ids={n.object_id for n in self.nodes}
        if len(ids)!=len(self.nodes):raise ValueError('duplicate dependency node')
        adj={k:[] for k in ids}
        for e in self.edges:
            if e.parent_object_id not in ids or e.child_object_id not in ids:raise ValueError('dependency target missing')
            adj[e.parent_object_id].append(e.child_object_id)
        done=set(); visiting=set()
        def walk(n):
            if n in visiting:raise ValueError('processing dependency cycle')
            if n in done:return
            visiting.add(n)
            for k in adj[n]:walk(k)
            visiting.remove(n);done.add(n)
        for n in ids:walk(n)
        return self
class RunItem(Closed):
    object_id: ID
    state: Literal['PLANNED','COMMITTED','ALREADY_COMMITTED','NEEDS_REVIEW','FAILED','DENIED','SKIPPED']
    generation_id: Hash | None
    error_code: str | None
    retryable: bool
class RunReport(Closed):
    schema_version: Literal['extension-run/1.0']
    request_id: ID
    attempt_id: ID
    job_key: Hash | None
    state: Literal['PLANNED','RUNNING','COMMITTED','ALREADY_COMMITTED','NEEDS_REVIEW','FAILED','DENIED']
    input_refs: list[FileRef]
    items: list[RunItem]
    started_at: AwareDatetime
    ended_at: AwareDatetime | None
    duration_seconds: FiniteFloat | None
    model_request_count: int = Field(ge=0)
    roi_round_count: int = Field(ge=0)
    inference_input_tokens: int | None = Field(ge=0)
    inference_output_tokens: int | None = Field(ge=0)
    environment_lock_ref: FileRef | None
    product_test_evidence_class: Literal['NOT_A_TEST','SYNTHETIC','REAL_ENGINE','BANK_JOINT']
    @model_validator(mode='after')
    def duration(self):
        if self.duration_seconds is not None and self.duration_seconds<0:raise ValueError('negative duration')
        if self.ended_at and self.ended_at<self.started_at:raise ValueError('run ends before it starts')
        return self
ROOT_MODELS={
 'source-access':SourceAccessState,'enhancement-review':ReviewDecision,
 'extension-manifest':ExtensionManifest,'enhancement-dependencies':DependencyManifest,'extension-run':RunReport,
 'source-version':SourceVersion,'source-event':SourceEvent,'source-checkpoint':SyncCheckpoint,
 'document-enhancement':Enhancement,'correction-patch':CorrectionPatch,
 'consumer-event':ConsumerEvent,'enhancement-release':ReleaseManifest,
}
