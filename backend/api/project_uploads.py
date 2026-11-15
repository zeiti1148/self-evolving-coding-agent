"""Bounded ZIP import and persistent, immutable project configurations."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from uuid import uuid4

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from backend.experience.database import connect
from backend.languages import detect_language
from backend.projects import ProjectDefinition, RunnerConfig, public_project
from backend.tools.workspace import inventory, ignored_path, reject_links

MAX_ARCHIVE = 64 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024


def safe_relative(value):
    path = PurePosixPath(value)
    reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
    if (not value or '\\' in value or ':' in value or path.is_absolute() or '..' in path.parts
            or any(ord(c) < 32 for c in value)
            or any(p.rstrip(' .') != p or p.split('.')[0].upper() in reserved for p in path.parts)):
        raise ValueError('ZIP 中包含不安全的文件路径。')
    return path


def extract_project(archive, destination, project_root=''):
    total = 0
    names = set()
    excluded = 0
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if not entries or len(entries) > 10000:
            raise ValueError('项目必须包含 1–10000 个 ZIP 条目。')
        for item in entries:
            relative = safe_relative(item.filename)
            mode = item.external_attr >> 16
            if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}) or item.flag_bits & 1:
                raise ValueError('ZIP 不支持链接、特殊文件或加密条目。')
            key = relative.as_posix().rstrip('/').casefold()
            if key in names:
                raise ValueError('ZIP 包含重复或大小写冲突的路径。')
            names.add(key)
            total += item.file_size
            if total > MAX_EXPANDED or item.file_size > 10_000_000:
                raise ValueError('解压后最多 256 MiB，单文件最多 10 MB。')
            if ignored_path(relative.as_posix()):
                excluded += 1
                continue
            target = destination.joinpath(*relative.parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(item) as incoming, target.open('xb') as outgoing:
                copied = 0
                while block := incoming.read(65536):
                    copied += len(block)
                    if copied > item.file_size or copied > 10_000_000:
                        raise ValueError('ZIP 文件大小与声明不一致。')
                    outgoing.write(block)
        root = destination
        if project_root:
            root = destination.joinpath(*safe_relative(project_root).parts)
        else:
            # GitHub ZIPs wrap the repository in one directory.
            for _ in range(5):
                try:
                    detect_language(root)
                    break
                except ValueError:
                    children = [p for p in root.iterdir() if p.name != '__MACOSX']
                    if len(children) != 1 or not children[0].is_dir():
                        break
                    root = children[0]
        language = detect_language(root)
        files = inventory(root)
        if not files:
            raise ValueError('项目没有可用源码。')
        digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
        return root, language, digest, len(files), excluded


class ConfigureProject(BaseModel):
    runner_config: RunnerConfig


class UploadRegistry:
    def __init__(self, settings, manager):
        self.settings, self.manager = settings, manager
        self.root = settings.workspace_root.parent / 'uploads'

    def python_runtimes(self):
        import sys
        root = Path(__file__).resolve().parents[2]
        candidates = {Path(self.settings.python_executable)}
        for pattern in ('*/*/pyvenv.cfg', '*/pyvenv.cfg'):
            for cfg in (root / '.cache').glob(pattern):
                candidates.add(cfg.parent / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python'))
        return [p for p in sorted(candidates) if p.is_file()]

    def compatible_python(self, source, config):
        """Match declared versions against local metadata, never import project code."""
        from importlib.metadata import distributions
        if not config.python_version or not config.requirements_file:
            return None
        required = source / config.requirements_file
        if not required.is_file() or required.stat().st_size > 100_000:
            return None
        normalize = lambda name: re.sub(r'[-_.]+', '-', name).lower()
        pins = {}
        for line in required.read_text(encoding='utf-8').splitlines():
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+)', line.strip())
            if not match:
                return None  # Complex requirements require explicit user configuration.
            pins[normalize(match[1])] = match[2]
        if not pins:
            return None
        for interpreter in self.python_runtimes():
            cfg = interpreter.parent.parent / 'pyvenv.cfg'
            if not cfg.is_file() or not re.search(r'^version(?:_info)?\s*=\s*' + re.escape(config.python_version) + r'\.', cfg.read_text(encoding='utf-8'), re.M):
                continue
            site = interpreter.parent.parent / 'Lib/site-packages'
            installed = {normalize(d.metadata.get('Name', '')): d.version for d in distributions(path=[str(site)])}
            if all(installed.get(name) == version for name, version in pins.items()):
                return str(interpreter)
        return None

    def initialize(self):
        with connect(self.settings.database_path) as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS uploaded_projects (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
            rows = connection.execute('SELECT data FROM uploaded_projects').fetchall()
        for row in rows:
            project = ProjectDefinition.model_validate_json(row[0])
            if project.source.resolve().is_relative_to(self.root.resolve()) and project.source.is_dir():
                self.register(project)

    def register(self, project):
        self.settings.projects[project.id] = project.source
        self.settings.project_definitions[project.id] = project
        self.manager.projects[project.id] = project.source

    def save(self, project):
        with connect(self.settings.database_path) as connection:
            connection.execute('INSERT INTO uploaded_projects VALUES (?,?)', (project.id, project.model_dump_json()))
        self.register(project)

    def routes(self, app):
        @app.post('/api/projects/upload', status_code=201)
        async def upload(request: Request, label: str = '', project_root: str = ''):
            if len(label) > 200 or len(project_root) > 500:
                raise HTTPException(422, '项目名称或子目录过长。')
            length = request.headers.get('content-length')
            if length and (not length.isdigit() or int(length) > MAX_ARCHIVE):
                raise HTTPException(413, 'ZIP 压缩包最多 64 MiB。')
            reject_links(self.root)
            self.root.mkdir(parents=True, exist_ok=True)
            folder = self.root / str(uuid4())
            folder.mkdir()
            success = False
            try:
                with tempfile.TemporaryFile() as stream:
                    size = 0
                    async for block in request.stream():
                        size += len(block)
                        if size > MAX_ARCHIVE:
                            raise HTTPException(413, 'ZIP 压缩包最多 64 MiB。')
                        stream.write(block)
                    stream.seek(0)
                    from starlette.concurrency import run_in_threadpool
                    source, language, digest, count, excluded = await run_in_threadpool(extract_project, stream, folder, project_root)
                identity = hashlib.sha256(json.dumps({k:v for k,v in inventory(source).items() if k != '.agent-upload.json'}, sort_keys=True).encode()).hexdigest()
                project = ProjectDefinition(id='upload-' + folder.name, label=label.strip() or source.name,
                    language=language, source=source, repository_id='uploaded/' + identity[:32],
                    origin='upload', source_digest=digest)
                manifest = source / '.agent-upload.json'
                if manifest.is_file() and manifest.stat().st_size <= 100_000:
                    hints = json.loads(manifest.read_text(encoding='utf-8'))
                    if not isinstance(hints, dict):
                        raise ValueError('项目上传配置必须是 JSON 对象。')
                    # Portable manifests describe tests, never host executables or shell commands.
                    config = RunnerConfig.model_validate(hints.get('runner_config', {}))
                    if config.python_executable:
                        raise ValueError('ZIP 配置不能指定本机解释器路径，请在页面选择。')
                    matched = self.compatible_python(source, config)
                    if matched:
                        config = config.model_copy(update={'python_executable': matched})
                    project = ProjectDefinition.model_validate({**project.model_dump(),
                        'runner_config': config, 'task_example': hints.get('task_description', '')})
                self.save(project)
                success = True
                return public_project(project, 'tree:' + digest) | {'file_count': count, 'excluded_entries': excluded}
            except (ValueError, OSError, zipfile.BadZipFile, RuntimeError) as exc:
                message = str(exc) if isinstance(exc, ValueError) else 'ZIP 无法读取，请检查文件格式及项目根目录。'
                raise HTTPException(400, message) from None
            finally:
                if not success and folder.resolve().parent == self.root.resolve():
                    shutil.rmtree(folder)

        @app.post('/api/projects/{identifier}/configure', status_code=201)
        def configure(identifier: str, body: ConfigureProject):
            project = self.settings.project_definitions.get(identifier)
            if project is None or project.origin != 'upload':
                raise HTTPException(404, '未找到上传项目。')
            if body.runner_config.python_executable and not Path(body.runner_config.python_executable).is_file():
                raise HTTPException(400, '所选 Python 解释器不存在。')
            if body.runner_config.python_version and not body.runner_config.python_executable:
                raise HTTPException(400, '该项目声明了 Python 版本，请选择对应的解释器。')
            # New immutable revision: changes never alter queued/running tasks.
            configured = project.model_copy(update={'id': 'upload-' + str(uuid4()), 'runner_config': body.runner_config})
            self.save(configured)
            return public_project(configured, 'tree:' + str(configured.source_digest))

        @app.get('/api/runtime-options')
        def runtime_options():
            from backend.llm_configuration import api_identity
            from backend.tools.native_runners import executable
            from backend.shared import ApplicationError
            try:
                executable('cmake'); executable('ctest')
                cpp = True
            except ApplicationError:
                cpp = False
            return {'python': [str(p) for p in self.python_runtimes()],
                    'go': bool(shutil.which('go')), 'cpp': cpp,
                    'archive_limit_mib': 64, 'api_configuration': api_identity(self.settings)}

