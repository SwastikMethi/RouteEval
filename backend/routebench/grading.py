"""External grading overlays with bounded subprocesses and explicit failure provenance."""
import asyncio
import fnmatch
import math
import os
import re
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from contextlib import suppress
from pathlib import Path, PurePosixPath

from .config import tree_hash
from .process import _leader_exited, stop_group
from .security import clean_environment, owned_delete, reject_symlinks, sanitize
from .workspace import MARKER, snapshot, write_snapshot

MAX_OUTPUT_BYTES = 10 * 1024 * 1024


def cleanup_stale(settings) -> int:
    """Remove orphaned private overlays after the runner acquires its exclusive startup lock."""
    root = Path(settings.workspaces).resolve()
    removed = 0
    for directory in sorted(root.glob('.grading-*')):
        marker = directory / MARKER
        if directory.is_symlink() or not directory.is_dir() or marker.is_symlink() or not marker.is_file():
            continue
        with marker.open('rb') as stream:
            if stream.read(64) != b'RouteBench grading scratch\n':
                continue
        owned_delete(root, directory)
        removed += 1
    return removed


async def _run(command, cwd, env, timeout, output_limit):
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-I', str(Path(__file__).with_name('watchdog.py')),
        '--parent-pid', str(os.getpid()), '--', *command,
        cwd=cwd, env=env, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    collected = bytearray()
    truncated = False

    async def drain():
        nonlocal truncated
        while chunk := await process.stdout.read(64 * 1024):
            remaining = max(0, output_limit - len(collected))
            collected.extend(chunk[:remaining])
            truncated |= len(chunk) > remaining

    reader = asyncio.create_task(drain())
    timed_out = False
    try:
        try:
            await asyncio.wait_for(_leader_exited(process), timeout)
        except asyncio.TimeoutError:
            timed_out = True
        finally:
            await asyncio.shield(stop_group(process, 1))
        try:
            await asyncio.wait_for(reader, 1)
        except asyncio.TimeoutError:
            truncated = True
    except BaseException:
        await asyncio.shield(stop_group(process, 1))
        reader.cancel()
        with suppress(asyncio.CancelledError):
            await reader
        raise
    output = collected.decode(errors='replace')
    if truncated:
        output += '\n[RouteBench: grader output truncated]\n'
    return process.returncode, output, timed_out


def _relative(value):
    if not isinstance(value, str) or not value or '\x00' in value or '\\' in value:
        raise ValueError('Grader paths must be relative POSIX paths')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or path == PurePosixPath('.'):
        raise ValueError('Grader path escapes the candidate root')
    return path.as_posix()


def _static(grader, candidate, patch):
    kind = grader['grader']
    if kind == 'diff_size':
        maximum = grader['max_lines']
        if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0:
            raise ValueError('diff_size requires a nonnegative max_lines')
        return patch['lines_added'] + patch['lines_removed'] <= maximum
    patterns = [_relative(path) for path in grader.get('paths', [])]
    if not patterns:
        raise ValueError('Path grader requires paths')
    changed = [_relative(path) for path in patch['changed_paths']]
    def matches(name):
        return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)
    if kind == 'allowed_paths':
        return all(matches(name) for name in changed)
    if kind == 'forbidden_paths':
        return not any(matches(name) for name in changed)
    entries = {path.relative_to(candidate).as_posix(): path for path in candidate.rglob('*')
               if '__hidden__' not in path.relative_to(candidate).parts}
    groups = [[path for name, path in entries.items() if fnmatch.fnmatchcase(name, pattern)]
              for pattern in patterns]
    if kind == 'file_exists':
        return all(groups)
    if kind == 'file_absent':
        return not any(groups)
    if kind in {'contains', 'excludes'}:
        pattern = grader['pattern']
        if not isinstance(pattern, str):
            raise ValueError('Content grader requires a string pattern')
        search = re.compile(pattern).search if grader.get('regex', False) else lambda value: pattern in value
        if not all(groups):
            return False
        for path in {path for group in groups for path in group}:
            if not path.is_file():
                return False
            found = bool(search(path.read_text(errors='replace')))
            if found != (kind == 'contains'):
                return False
        return True
    raise ValueError('Unsupported deterministic grader')


def _pytest_command(arguments, candidate, config, junit):
    if not arguments or not all(isinstance(item, str) and item and '\x00' not in item for item in arguments):
        raise ValueError('Grader command must be a nonempty argument array')
    command = [sys.executable if item == '{python}' else item for item in arguments]
    if command[0] in {'python', 'python3'}:
        command[0] = sys.executable
    if Path(command[0]).name == 'pytest':
        command = [sys.executable, '-m', 'pytest', *command[1:]]
    pytest_command = len(command) > 2 and command[0] == sys.executable and command[1:3] == ['-m', 'pytest']
    if pytest_command:
        # -I loads trusted pytest before the controlled config adds candidate imports.
        command.insert(1, '-I')
        command.extend(['-c', str(config), '--rootdir', str(candidate), '--noconftest',
                        '--tb=no', '--assert=plain', '--show-capture=no', '--capture=fd',
                        '-p', 'no:cacheprovider', '-o', 'addopts=',
                        '-o', 'junit_logging=no', '-o', 'junit_log_passing_tests=false',
                        f'--junitxml={junit}'])
    return command, pytest_command


def _junit_score(path, partial):
    if path.stat().st_size > MAX_OUTPUT_BYTES:
        raise ValueError('JUnit report exceeds its size limit')
    root = ET.parse(path).getroot()
    cases = list(root.iter('testcase'))
    if not cases:
        return 0., False, 'No tests completed'
    failures = sum(any(case.find(tag) is not None for tag in ('failure', 'error', 'skipped')) for case in cases)
    passed = len(cases) - failures
    score = passed / len(cases) if partial else float(failures == 0)
    return score, failures == 0, f'{passed}/{len(cases)} tests passed'


def _result(grader, *, score=0., passed=False, summary='', output='', duration_ms=0., infrastructure_error=False):
    return {'id': grader['id'], 'component': grader['component'], 'weight': grader['weight'],
            'score': score, 'passed': passed, 'mandatory': grader.get('mandatory', True),
            'summary': summary, 'output': output, 'duration_ms': duration_ms,
            'infrastructure_error': infrastructure_error}


def _failed(case, message, infrastructure):
    results = [_result(grader, summary=sanitize(message), infrastructure_error=infrastructure)
               for grader in case['scoring']]
    return {'graders': results, 'score': None if infrastructure else 0.,
            'full_pass': False, 'infrastructure_error': infrastructure}


async def grade(case: dict, workspace: Path, patch: dict, settings) -> dict:
    """Grade only after the agent process exits; this function never touches its repository."""
    try:
        candidate_files = snapshot(Path(workspace))
        if (Path(workspace) / '__hidden__').exists():
            return _failed(case, 'Candidate created the reserved hidden-grader path', False)
    except (OSError, ValueError) as error:
        return _failed(case, str(error), False)
    try:
        hidden = Path(case['hidden_graders'])
        if not hidden.is_dir():
            raise ValueError('Hidden grader fixture is missing')
        reject_symlinks(hidden)
        if case.get('hidden_hash') and tree_hash(hidden) != case['hidden_hash']:
            raise ValueError('Hidden grader content hash changed after suite loading')
        hidden_files = snapshot(hidden)
        root = Path(settings.workspaces).resolve()
        root.mkdir(parents=True, exist_ok=True)
        timeout = float(settings.execution.get('grader_timeout_seconds', 60))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('Grader timeout must be a finite positive number')
        timeout = min(timeout, 60.)
        output_limit = min(MAX_OUTPUT_BYTES, max(1, int(settings.execution.get('max_artifact_bytes', MAX_OUTPUT_BYTES))))
        temporary = Path(tempfile.mkdtemp(prefix='.grading-', dir=root))
        (temporary / MARKER).write_text('RouteBench grading scratch\n')
    except (OSError, ValueError) as error:
        return _failed(case, str(error), True)
    try:
        candidate = temporary / 'candidate'
        candidate.mkdir()
        write_snapshot(candidate_files, candidate)
        (candidate / '__hidden__').mkdir()
        write_snapshot(hidden_files, candidate / '__hidden__')
        config = temporary / 'pytest.ini'
        config.write_text(f'[pytest]\npythonpath = {candidate}\n')
        env = clean_environment()
        env.update({'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'PYTHONNOUSERSITE': '1',
                    'PYTEST_ADDOPTS': '', 'PYTEST_PLUGINS': '', 'PYTHONDONTWRITEBYTECODE': '1'})
        results = []
        for index, grader in enumerate(case['scoring']):
            started = time.perf_counter()
            output = ''
            infrastructure = False
            score, passed = 0., False
            try:
                reject_symlinks(candidate)
                if 'command' not in grader:
                    passed = _static(grader, candidate, patch)
                    score = float(passed)
                    summary = 'Constraint satisfied' if passed else 'Constraint failed'
                else:
                    arguments = grader['command']
                    for argument in arguments:
                        if argument.startswith('__hidden__/'):
                            relative = _relative(argument.split('::', 1)[0])
                            if not (candidate / relative).exists():
                                raise FileNotFoundError('Configured hidden grader is missing')
                    junit = temporary / f'result-{index}.xml'
                    command, is_pytest = _pytest_command(arguments, candidate, config, junit)
                    returncode, output, timed_out = await _run(command, candidate, env, timeout, output_limit)
                    if timed_out:
                        summary = 'Grader timed out; candidate received no credit'
                    elif is_pytest:
                        if returncode in (3, 4) or 'No module named pytest' in output:
                            infrastructure = True
                            summary = 'Pytest runner or command configuration failed'
                        elif junit.is_file() and returncode in (0, 1, 2, 5):
                            score, passed, summary = _junit_score(junit, grader.get('partial_credit', False))
                            passed = passed and returncode == 0
                            if not passed and not grader.get('partial_credit', False):
                                score = 0.
                        else:
                            summary = 'Candidate tests could not complete'
                    else:
                        passed = returncode == 0
                        score = float(passed)
                        summary = 'Command passed' if passed else f'Command failed (exit {returncode})'
            except (OSError, ValueError, ET.ParseError, KeyError) as error:
                infrastructure = True
                summary = 'Grader infrastructure failure: ' + str(error)
            output = output.replace(str(temporary), '[grading]')
            results.append(_result(grader, score=score, passed=passed,
                                   summary=sanitize(summary), output=sanitize(output),
                                   duration_ms=(time.perf_counter() - started) * 1000,
                                   infrastructure_error=infrastructure))
        infrastructure = any(result['infrastructure_error'] for result in results)
        total = sum(result['weight'] * result['score'] for result in results)
        full = not infrastructure and total + 1e-9 >= case.get('pass_threshold', 1.) and all(
            result['passed'] for result in results if result['mandatory'])
        return {'graders': results, 'score': None if infrastructure else round(total, 10),
                'full_pass': full, 'infrastructure_error': infrastructure}
    except (OSError, ValueError) as error:
        return _failed(case, sanitize(str(error)), True)
    finally:
        owned_delete(root, temporary)
