#!/usr/bin/env python3
"""DOC2MD-19CAP 采用登记生成器（TL 侧，adoption 批次）。

用途：由只读来源快照 PACK 与采用后的工作副本计算
  - adoption/PATH_MAP.json           来源路径/哈希 -> 工作路径/哈希 + 引用基准
  - adoption/ADOPTED_MANIFEST.json   采用后工作文件的独立清单（不替代原 MANIFEST.json）

不变式：
  1) 原包 MANIFEST.json 只用于证明“来源字节”，不得用于证明已改路径的工作副本仍逐字节相同。
  2) 工作副本哈希一律现算，不抄原清单。
  3) PACK-only 文件（tools/、validation/、MANIFEST.json、adoption/SPEC_INDEX_APPEND_CANDIDATE.md）
     记为 PACK_ONLY，表示不搬迁、不产生工作副本。

用法（仓库根执行）：
  python docs/proposals/doc2md-19cap/v1.0.0/adoption/build_adoption_records.py
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

REPO = subprocess.run(['git', '-C', os.path.dirname(os.path.abspath(__file__)),
                       'rev-parse', '--show-toplevel'],
                      capture_output=True, text=True, check=True).stdout.strip()
PACK_REL = 'docs/architecture/DOC2MD_19CAP_SDD_LOOP_V1.0_20261017'
DOC_REL = 'docs/proposals/doc2md-19cap/v1.0.0'
ZIP_REL = 'docs/architecture/doc2md_19项能力_SDD与Loop工程契约包_V1.0_20261017.zip'
EXPECTED_ZIP_SHA = 'f196a238b203e272a23a4a9c4e1485edf3d233ea329f340eb6e6ee7a30ccfc51'

# 单个文件的显式搬迁移位（必须是穷举，不做通配猜测）
EXPLICIT = {
    '00_READ_ME_FIRST.md': f'{DOC_REL}/00_READ_ME_FIRST.md',
    '01_SDD.md': f'{DOC_REL}/01_SDD.md',
    '02_LOOP_ENGINEERING.md': f'{DOC_REL}/02_LOOP_ENGINEERING.md',
    '03_CHANGE_DECISIONS.md': f'{DOC_REL}/03_CHANGE_DECISIONS.md',
    '04_TRACEABILITY.csv': f'{DOC_REL}/04_TRACEABILITY.csv',
    '04_TRACEABILITY.md': f'{DOC_REL}/04_TRACEABILITY.md',
    '05_TEST_ACCEPTANCE.md': f'{DOC_REL}/05_TEST_ACCEPTANCE.md',
    '06_FRAMEWORKS.md': f'{DOC_REL}/06_FRAMEWORKS.md',
    '07_OPEN_INPUTS.md': f'{DOC_REL}/07_OPEN_INPUTS.md',
    '08_CODEBUDDY_TL_PROMPT.md': f'{DOC_REL}/08_CODEBUDDY_TL_PROMPT.md',
    '09_DECISION_ALIGNMENT.md': f'{DOC_REL}/09_DECISION_ALIGNMENT.md',
    'VALIDATION_REPORT.md': f'{DOC_REL}/VALIDATION_REPORT.md',
}

# 目录前缀搬移
PREFIX = {
    'inputs/': f'{DOC_REL}/inputs/',
    'spec-proposal/': 'specs/proposals/doc2md-19cap-v1.0.0/',
    'contracts/': 'contracts/doc2md-19cap/v1.0.0/',
    'loop/': 'loop/proposals/doc2md-19cap/v1.0.0/',
}

# 不回搬迁：保留在 PACK 内运行/取证
PACK_ONLY = [
    'MANIFEST.json',
    'tools/check_delivery.py',
    'tools/check_baseline.py',
    'tools/check_loop_diff.py',
    'tools/test_scope_guard.py',
    'validation/contract_package_self_check.json',
    'validation/current_loop_scope_check.json',
    'validation/protected_baseline_check.json',
    'validation/scope_guard_synthetic_self_test.json',
    'adoption/SPEC_INDEX_APPEND_CANDIDATE.md',
]

# 已登记的工作副本路径调整（ADOPT-PATH-01），其余工作文件应与来源逐字节相同
PATH_EDITS = {
    'loop/proposals/doc2md-19cap/v1.0.0/loop_contract.json': (
        'baseline_lock / protected_existing_files_ref 由 ../inputs/baseline_lock.json '
        '改为 ../../../../docs/proposals/doc2md-19cap/v1.0.0/inputs/baseline_lock.json；'
        'traceability / test_cases 保持同目录引用'
    ),
    'specs/proposals/doc2md-19cap-v1.0.0/spec.md': '../01_SDD.md -> ../../../docs/proposals/doc2md-19cap/v1.0.0/01_SDD.md',
    'specs/proposals/doc2md-19cap-v1.0.0/plan.md': '../02_LOOP_ENGINEERING.md -> ../../../docs/proposals/doc2md-19cap/v1.0.0/02_LOOP_ENGINEERING.md',
    'specs/proposals/doc2md-19cap-v1.0.0/tasks.md': '../02_LOOP_ENGINEERING.md -> ../../../docs/proposals/doc2md-19cap/v1.0.0/02_LOOP_ENGINEERING.md',
    'specs/proposals/doc2md-19cap-v1.0.0/acceptance.md': '../05_TEST_ACCEPTANCE.md -> ../../../docs/proposals/doc2md-19cap/v1.0.0/05_TEST_ACCEPTANCE.md',
    'specs/proposals/doc2md-19cap-v1.0.0/test_cases.md': '../05_TEST_ACCEPTANCE.md -> ../../../docs/proposals/doc2md-19cap/v1.0.0/05_TEST_ACCEPTANCE.md',
}


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def collect_pack_files() -> list[str]:
    out = []
    root = os.path.join(REPO, PACK_REL)
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = os.path.join(dirpath, name)
            out.append(os.path.relpath(full, root).replace(os.sep, '/'))
    return sorted(out)


def work_path_for(source_rel: str) -> str | None:
    if source_rel in EXPLICIT:
        return EXPLICIT[source_rel]
    for pre, target in PREFIX.items():
        if source_rel.startswith(pre):
            return target + source_rel[len(pre):]
    if source_rel in PACK_ONLY:
        return None
    raise SystemExit(f'未登记搬移规则的来源文件：{source_rel}')


def main() -> int:
    zip_path = os.path.join(REPO, ZIP_REL)
    zip_sha = sha256(zip_path)
    head = subprocess.run(['git', '-C', REPO, 'rev-parse', 'HEAD'],
                          capture_output=True, text=True, check=True).stdout.strip()

    original = json.load(open(os.path.join(REPO, PACK_REL, 'MANIFEST.json'), encoding='utf-8'))
    original_sha = {f['path']: f['sha256'] for f in original['files']}

    entries = []
    for rel in collect_pack_files():
        src_full = os.path.join(REPO, PACK_REL, rel)
        src_sha = sha256(src_full)
        declared = original_sha.get(rel)
        work_rel = work_path_for(rel)
        entry = {
            'source_path': f'{PACK_REL}/{rel}',
            'source_sha256': src_sha,
            'source_sha256_matches_pack_manifest': (declared == src_sha) if declared else None,
            'work_path': work_rel,
            'work_sha256': None,
            'relocated': work_rel is not None,
            'content_identical': None,
            'path_edit': PATH_EDITS.get(work_rel) if work_rel else None,
        }
        if work_rel is not None:
            work_full = os.path.join(REPO, work_rel)
            if not os.path.exists(work_full):
                raise SystemExit(f'工作副本缺失：{work_rel}')
            work_sha = sha256(work_full)
            entry['work_sha256'] = work_sha
            entry['content_identical'] = (work_sha == src_sha)
        entries.append(entry)

    relocated = [e for e in entries if e['relocated']]
    identical = [e for e in relocated if e['content_identical']]
    edited = [e for e in relocated if not e['content_identical']]

    path_map = {
        'schema_version': 'doc2md-19cap-path-map/1.0',
        'workflow': 'DOC2MD-19CAP',
        'adoption_batch': 'DOC_IMPORT',
        'recorded_at_commit': head,
        'source_zip': {'path': ZIP_REL, 'sha256': zip_sha,
                       'expected_sha256': EXPECTED_ZIP_SHA,
                       'matches_expected': zip_sha == EXPECTED_ZIP_SHA},
        'source_package_root': PACK_REL,
        'reference_basis': 'All relative references in the working copy are resolved relative to the '
                           'directory containing the referencing file.',
        'counts': {
            'pack_files': len(entries),
            'relocated_to_work_copy': len(relocated),
            'pack_only': len(entries) - len(relocated),
            'work_copy_content_identical': len(identical),
            'work_copy_with_registered_path_edit': len(edited),
        },
        'entries': entries,
    }

    adopted = {
        'schema_version': 'doc2md-19cap-adopted-manifest/1.0',
        'note': 'Independent manifest of the adopted working copies. Does NOT replace '
                'PACK/MANIFEST.json and must not be used to claim the original package files '
                'are unchanged after relocation.',
        'source_manifest': f'{PACK_REL}/MANIFEST.json',
        'source_manifest_package_id': original.get('package_id'),
        'source_manifest_status': original.get('status'),
        'recorded_at_commit': head,
        'source_zip_sha256': zip_sha,
        'files': [
            {
                'work_path': e['work_path'],
                'sha256': e['work_sha256'],
                'source_path': e['source_path'],
                'source_sha256': e['source_sha256'],
                'content_identical_to_source': e['content_identical'],
                'path_edit': e['path_edit'],
            }
            for e in relocated
        ],
        'not_copied': [{'source_path': f'{PACK_REL}/{r}', 'reason': 'PACK_ONLY'} for r in PACK_ONLY],
    }

    out_dir = os.path.join(REPO, DOC_REL, 'adoption')
    with open(os.path.join(out_dir, 'PATH_MAP.json'), 'w', encoding='utf-8') as fh:
        json.dump(path_map, fh, ensure_ascii=False, indent=2)
        fh.write('\n')
    with open(os.path.join(out_dir, 'ADOPTED_MANIFEST.json'), 'w', encoding='utf-8') as fh:
        json.dump(adopted, fh, ensure_ascii=False, indent=2)
        fh.write('\n')

    print(json.dumps({'pack_files': len(entries), 'relocated': len(relocated),
                      'identical': len(identical), 'path_edited': len(edited),
                      'zip_sha_ok': zip_sha == EXPECTED_ZIP_SHA,
                      'pack_manifest_mismatch': [e['source_path'] for e in entries
                                                 if e['source_sha256_matches_pack_manifest'] is False]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
