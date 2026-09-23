"""Release checks for every fixture, reference patch, and intentional bad mutation."""
import fnmatch
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

SUITE = Path(__file__).resolve().parents[1] / 'evals' / 'python-core'
MANIFEST = json.loads((SUITE / 'suite.yaml').read_text())
CASES = MANIFEST['cases']
MUTATIONS = json.loads((SUITE / 'reference-solutions' / 'mutations.json').read_text())


def files_at(directory):
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in directory.rglob('*') if path.is_file()
            and '__pycache__' not in path.parts and '.pytest_cache' not in path.parts
            and '__hidden__' not in path.parts}


def test_suite_release_metadata_and_privacy():
    assert [case['id'] for case in CASES] == [f'RB-PY-{index:03}' for index in range(1, 10)]
    assert [case['id'] for case in CASES if 'smoke' in case['tags']] == ['RB-PY-001', 'RB-PY-003', 'RB-PY-005']
    assert [case['id'] for case in CASES if 'planning' in case['tags']] == ['RB-PY-009']
    assert Counter(case['difficulty'] for case in CASES) == {'easy': 3, 'medium': 3, 'hard': 3}
    assert Counter(case['category'] for case in CASES) == {'bug-fix': 2, 'feature': 2, 'multi-file': 2, 'refactor': 1, 'edge-case': 1, 'planning-execution': 1}
    assert MANIFEST['version'] == 1 and MANIFEST['release'] == '1.1.0'
    assert MANIFEST['pass_threshold'] == 1.0
    for case in CASES:
        fixture = SUITE / case['fixture']
        assert (fixture / 'README.md').is_file()
        assert (fixture / 'requirements.txt').is_file()
        assert (fixture / '.gitignore').is_file()
        assert not any(path.is_symlink() for path in fixture.rglob('*'))
        assert not any(part in {'__hidden__', 'hidden-graders', 'reference-solutions'}
                       for path in fixture.rglob('*') for part in path.relative_to(fixture).parts)
        weights = Counter()
        for grader in case['scoring']:
            weights[grader['component']] += grader['weight']
            assert grader['mandatory'] is True
            if 'command' in grader:
                assert grader['command'][:3] == ['{python}', '-m', 'pytest']
                assert '--tb=no' in grader['command'] and '--assert=plain' in grader['command']
        assert weights == pytest.approx({'functional': .8, 'regression': .1, 'constraints': .1})
        assert len([grader for grader in case['scoring'] if grader['component'] == 'functional']) >= 2
        assert (SUITE / case['reference_patch']).is_file()
        assert case['id'] in MUTATIONS


@pytest.mark.parametrize('case', CASES, ids=lambda case: case['id'])
@pytest.mark.parametrize('variant', ['baseline', 'reference', 'mutation'])
def test_baselines_references_and_mutations(case, variant, tmp_path):
    workspace = tmp_path / 'candidate'
    shutil.copytree(SUITE / case['fixture'], workspace)
    before = files_at(workspace)
    if variant != 'baseline':
        applied = subprocess.run(['git', 'apply', str(SUITE / case['reference_patch'])],
                                 cwd=workspace, capture_output=True, text=True, timeout=15)
        assert applied.returncode == 0, applied.stderr
    if variant in {'missing-plan', 'missing-verification'}:
        (workspace / ('PLAN.md' if variant == 'missing-plan' else 'VERIFICATION.md')).unlink()
    if variant == 'mutation':
        mutation = MUTATIONS[case['id']]
        source = workspace / mutation['path']
        content = source.read_text()
        assert content.count(mutation['old']) == 1, mutation['reason']
        source.write_text(content.replace(mutation['old'], mutation['new']))
    after = files_at(workspace)
    changed = {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}
    shutil.copytree(SUITE / case['hidden_graders'], workspace / '__hidden__')
    # Execute only the copy, with trusted pytest configuration and no user plugins.
    config = tmp_path / 'pytest.ini'
    config.write_text('[pytest]\n')
    env = {**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
    env.pop('PYTEST_ADDOPTS', None)
    env.pop('PYTEST_PLUGINS', None)
    env.pop('PYTHONPATH', None)
    outcomes = {}
    for grader in case['scoring']:
        if 'command' in grader:
            command = [sys.executable if arg == '{python}' else arg for arg in grader['command']]
            command += ['-c', str(config), '-p', 'no:cacheprovider']
            completed = subprocess.run(command, cwd=workspace, env=env, capture_output=True,
                                       text=True, timeout=30)
            # Test failures (1) are evidence; collection/usage errors invalidate a case.
            assert completed.returncode in (0, 1), (case['id'], variant, grader['id'], completed.stdout, completed.stderr)
            outcomes[grader['id']] = completed.returncode == 0
        else:
            assert grader['grader'] == 'allowed_paths'
            outcomes[grader['id']] = all(any(fnmatch.fnmatchcase(path, pattern) for pattern in grader['paths']) for path in changed)
    functional = [outcomes[grader['id']] for grader in case['scoring'] if grader['component'] == 'functional']
    assert outcomes['source_scope']
    assert outcomes['regression'], (case['id'], variant, 'visible regression')
    if variant == 'reference':
        assert all(outcomes.values()), (case['id'], outcomes)
    elif variant in {'missing-plan', 'missing-verification'}:
        assert all(functional), (case['id'], variant, outcomes)
        assert not outcomes['runtime_contract'], (case['id'], variant, outcomes)
    else:
        assert not all(functional), (case['id'], variant, outcomes)
        if case['id'] == 'RB-PY-007' and variant == 'baseline':
            assert outcomes['behavior'] and not outcomes['structure']


@pytest.mark.parametrize('variant', ['missing-plan', 'missing-verification'])
def test_planning_requires_workflow_artifacts(variant, tmp_path):
    case = next(case for case in CASES if case['id'] == 'RB-PY-009')
    test_baselines_references_and_mutations(case, variant, tmp_path)
