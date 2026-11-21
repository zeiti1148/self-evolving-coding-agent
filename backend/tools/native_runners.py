"""Native Go / CMake runners with fresh machine-readable test evidence."""
import json
import shutil
import sys
import time
from pathlib import Path
from uuid import uuid4

from backend.shared import ApplicationError, ErrorCode
from .process import run_process
from .workspace import reject_links


def executable(name):
    result = shutil.which(name)
    local = Path(sys.executable).parent / (name + ('.exe' if sys.platform == 'win32' else ''))
    if not result and local.is_file():
        result = str(local)
    if not result:
        raise ApplicationError(ErrorCode.COMMAND_FAILED, f'未找到 {name}，请安装对应测试工具链。')
    return result


def go_verdict(output, metadata, code):
    cases, problems = {}, []
    package_failed = False
    try:
        for line in output.splitlines():
            if not line.startswith('{'):
                continue
            event = json.loads(line)
            action, test = event.get('Action'), event.get('Test')
            if test and action in {'pass', 'fail', 'skip'}:
                cases[(event.get('Package', ''), test)] = action
            if action == 'fail' and not test:
                package_failed = True
            if event.get('Output') and len(problems) < 100:
                problems.append(event['Output'])
    except (ValueError, TypeError):
        return output, metadata, ErrorCode.COMMAND_FAILED
    counts = dict(tests=len(cases), failures=sum(v == 'fail' for v in cases.values()), errors=0,
                  skipped=sum(v == 'skip' for v in cases.values()))
    metadata.update(counts, runner='go', error_types=['AssertionError'] if counts['failures'] else [],
        test_failures=[{'test': p + '.' + t, 'error_type': 'AssertionError', 'message': ''.join(problems)[-1500:]}
                       for (p, t), state in cases.items() if state == 'fail'][:20])
    if code == ErrorCode.COMMAND_TIMEOUT:
        return output, metadata, code
    if metadata.get('truncated') or not cases or counts['skipped']:
        return output, metadata, ErrorCode.COMMAND_FAILED
    if counts['failures']:
        return output, metadata, ErrorCode.TEST_FAILED
    return output, metadata, ErrorCode.COMMAND_FAILED if package_failed or code else None


def run_native_tests(service, record, cwd, timeout, runner):
    if runner == 'go':
        output, metadata, code = run_process([executable('go'), 'test', '-json', '-count=1', '-mod=readonly', './...'],
            cwd, timeout, limit=500_000, extra_env={'GOENV': 'off', 'GOWORK': 'off', 'GOTOOLCHAIN': 'local'})
        service._verify_tests(record)
        metadata['changed_files'] = service.manager.changed_files(record.path)
        return go_verdict(output, metadata, code)

    root = service.manager.root / '.native-builds' / record.path.name
    reject_links(root)
    root.mkdir(parents=True, exist_ok=True)
    report = root / (str(uuid4()) + '.xml')
    build = root / 'build'
    cmake, ctest = executable('cmake'), executable('ctest')
    configure = [cmake, '-S', str(cwd), '-B', str(build), '-DCMAKE_BUILD_TYPE=Debug', '-DBUILD_TESTING=ON']
    if not (build / 'CMakeCache.txt').exists():
        try:
            ninja = executable('ninja')
        except ApplicationError:
            ninja = None
        if ninja:
            # Ninja does not discover an installed toolchain on its own: without a
            # compiler on PATH it fails before the default generator would.
            compiler = next((shutil.which(name) for name in ('g++', 'clang++', 'cl') if shutil.which(name)), None)
            if compiler:
                configure += ['-G', 'Ninja', '-DCMAKE_MAKE_PROGRAM=' + ninja]
                cxx = compiler if not compiler.endswith('cl.exe') else compiler
                configure += ['-DCMAKE_CXX_COMPILER=' + cxx]
                if shutil.which('gcc'):
                    configure += ['-DCMAKE_C_COMPILER=' + shutil.which('gcc')]
    commands = [configure, [cmake, '--build', str(build), '--config', 'Debug', '--parallel', '2'],
        [ctest, '--test-dir', str(build), '-C', 'Debug', '--output-on-failure', '--no-tests=error', '--output-junit', str(report)]]
    started, outputs = time.monotonic(), []
    metadata = {'runner': 'ctest'}
    for argv in commands:
        remaining = max(1, int(timeout - (time.monotonic() - started)))
        output, result, code = run_process(argv, cwd, remaining)
        outputs.append(output)
        metadata.update(result)
        metadata['runner'] = 'ctest'
        service._verify_tests(record)
        if code:
            break
    metadata.update(changed_files=service.manager.changed_files(record.path), duration_ms=round((time.monotonic() - started)*1000))
    output = '\n'.join(outputs)[-50_000:]
    if code == ErrorCode.COMMAND_TIMEOUT or not report.is_file():
        return output, metadata, code or ErrorCode.COMMAND_FAILED
    return service._test_verdict(output, metadata, code, [report], 'ctest')
