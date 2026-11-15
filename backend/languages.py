"""Language identifiers and verifiable test runners shared by API and agents."""
from typing import Literal

ProjectLanguage = Literal['java', 'python', 'go', 'cpp']
TestRunner = Literal['maven', 'pytest', 'go', 'ctest']
RUNNERS = {'java': 'maven', 'python': 'pytest', 'go': 'go', 'cpp': 'ctest'}
MARKERS = {'java': ('pom.xml',), 'python': ('pyproject.toml', 'pytest.ini', 'setup.cfg', 'setup.py'),
           'go': ('go.mod',), 'cpp': ('CMakeLists.txt',)}
SOURCE_SUFFIXES = {'java': {'.java'}, 'python': {'.py'}, 'go': {'.go'},
                   'cpp': {'.c', '.cc', '.cpp', '.cxx', '.h', '.hpp', '.hxx'}}

def detect_language(root):
    found = [language for language, markers in MARKERS.items() if any((root / name).is_file() for name in markers)]
    if len(found) != 1:
        raise ValueError('请选择包含单个构建系统的项目根目录（Maven、pytest、Go Modules 或 CMake）。')
    return found[0]
