"""Deterministic, independently specified Java/Python repair fixtures for the ablation.

The oracle is the literal case table, never the implementation or generated experience.
Hidden cases are kept outside the agent's registered source directory.
"""
import json
from pathlib import Path


KINDS = ['readonly', 'writable', 'independent', 'numeric-v1', 'numeric-v2', 'dependency']
EVALUATION = ['conditional', 'independent', 'numeric-v2', 'dependency']


def examples(kind, hidden=False):
    pad, name, integer = ('\t ', 'Mira', '17') if hidden else (' ', 'Ada', '3')
    cases=[]
    def add(fn,args,expected=None,raises=False):
        cases.append(dict(fn=fn,args=args,expected=expected,raises=raises))
    if kind in {'readonly','writable','conditional'}:
        modes=['read','write'] if kind=='conditional' else ['read' if kind=='readonly' else 'write']
        for mode in modes:
            for value in [None,'',pad]:
                add('label',[value,mode], 'guest' if mode=='read' else None, mode=='write')
            add('label',[pad+name+pad,mode],name)
            add('label',[name,mode],name)
    elif kind=='independent':
        for value in [None,'',pad]: add('label',[value],'guest')
        add('label',[pad+name+pad],name)
        for value in [None,'',pad,'bad','-2']: add('quantity',[value],0)
        add('quantity',[pad+integer+pad],int(integer))
        add('quantity',['0'],0)
    elif kind.startswith('numeric'):
        strict=kind.endswith('v2')
        for value in [None,'',pad,'bad','-2','2.5']:
            add('parse_number',[value],None if strict else 0,strict)
        add('parse_number',[pad+integer+pad],int(integer))
        add('parse_number',['0'],0)
    else:
        for value in [None,'',pad]:
            add('normalize',[value],'guest')
            add('render',[value],'Welcome, guest!')
        add('normalize',[pad+name+pad],name)
        add('render',[pad+name+pad],'Welcome, '+name+'!')
    return cases


def contract(kind):
    if kind in {'readonly','writable','conditional'}:
        modes={'readonly':'read 模式','writable':'write 模式','conditional':'read 和 write 两种模式'}[kind]
        return f'label(value, mode)：{modes}。read 模式遇到空值或去空白后为空时返回 guest；write 模式相同输入必须抛出输入异常；有效文本去除首尾空白且保留大小写。'
    if kind=='independent':
        return '同一文件中两个独立函数：label 空值或空白返回 guest，有效文本去除首尾空白且保留大小写；quantity 空值、空白、非整数或负数返回 0，有效非负整数去空白后解析。两个规则互不依赖。'
    if kind.startswith('numeric'):
        action='抛出输入异常' if kind.endswith('v2') else '返回 0'
        return f'parse_number：非负十进制整数字符串去除首尾空白后解析；空值、空白、非整数或负数必须{action}。这是明确的 {kind} 契约。'
    return 'normalize：空值或空白返回 guest，有效文本去除首尾空白；render 必须先调用 normalize，再返回 Welcome, <规范化名称>!。调用顺序是显式依赖，不能只修复其中一个函数。'


def python_source(kind):
    if kind in {'readonly','writable','conditional'}:
        return 'def label(value, mode):\n    return value.strip()\n'
    if kind=='independent':
        return 'def label(value):\n    return value.strip()\n\ndef quantity(value):\n    return int(value)\n'
    if kind.startswith('numeric'):
        return 'def parse_number(value):\n    return int(value)\n'
    return 'def normalize(value):\n    return value.strip()\n\ndef render(value):\n    return "Welcome, " + value + "!"\n'


def java_source(kind):
    if kind in {'readonly','writable','conditional'}:
        methods='public static String label(String value, String mode) { return value.trim(); }'
    elif kind=='independent':
        methods='public static String label(String value) { return value.trim(); }\n public static int quantity(String value) { return Integer.parseInt(value); }'
    elif kind.startswith('numeric'):
        methods='public static int parse_number(String value) { return Integer.parseInt(value); }'
    else:
        methods='public static String normalize(String value) { return value.trim(); }\n public static String render(String value) { return "Welcome, " + value + "!"; }'
    return 'package dev.selfevolving.benchmark;\npublic class Rules {\n '+methods+'\n}\n'


def test_source(language,cases):
    if language=='python':
        lines=['import pytest','import rules','']
        for i,case in enumerate(cases):
            lines.append(f'def test_case_{i}():')
            call='rules.'+case['fn']+'('+', '.join(repr(v) for v in case['args'])+')'
            lines.extend(['    with pytest.raises(ValueError):','        '+call] if case['raises'] else
                         ['    assert '+call+' == '+repr(case['expected'])])
        return '\n'.join(lines)+'\n'
    lines=['package dev.selfevolving.benchmark;','import org.junit.jupiter.api.Test;',
           'import static org.junit.jupiter.api.Assertions.*;','class RulesTest {']
    for i,case in enumerate(cases):
        call='Rules.'+case['fn']+'('+', '.join('null' if v is None else json.dumps(v) for v in case['args'])+')'
        assertion='assertThrows(IllegalArgumentException.class, () -> '+call+');' if case['raises'] else \
            'assertEquals('+json.dumps(case['expected'])+', '+call+');'
        lines.append(f' @Test void case{i}() {{ {assertion} }}')
    return '\n'.join([*lines,'}'])+'\n'


def generate(root: Path, repository_root: Path):
    root.mkdir(parents=True,exist_ok=True)
    catalog=[]
    for phase,kinds in [('training',KINDS),('evaluation',EVALUATION)]:
        for language in ['java','python']:
            for kind in kinds:
                identifier=f'{phase}-{language}-{kind}'
                directory=root/identifier
                directory.mkdir(exist_ok=True)
                visible,hidden=examples(kind),examples(kind,True)
                if language=='python':
                    source='rules.py';test='tests/test_rules.py'
                    (directory/'pyproject.toml').write_text('[project]\nname="knowledge-fixture"\nversion="0.1.0"\n[tool.pytest.ini_options]\ntestpaths=["tests"]\npythonpath=["."]\n',encoding='utf-8')
                else:
                    source='src/main/java/dev/selfevolving/benchmark/Rules.java'
                    test='src/test/java/dev/selfevolving/benchmark/RulesTest.java'
                    (directory/'pom.xml').write_bytes((repository_root/'benchmarks/java-parse-count/pom.xml').read_bytes())
                for name,content in [(source,python_source(kind) if language=='python' else java_source(kind)),(test,test_source(language,visible))]:
                    path=directory/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content,encoding='utf-8')
                hidden_path=root/'heldout-tests'/identifier/Path(test).name
                hidden_path.parent.mkdir(parents=True,exist_ok=True)
                hidden_path.write_text(test_source(language,hidden),encoding='utf-8')
                detail=contract(kind)+' Python 输入异常为 ValueError，Java 为 IllegalArgumentException。请修复应用源码，通过全部原始测试，不得修改测试和构建文件。'
                (directory/'README.md').write_text(detail,encoding='utf-8')
                catalog.append(dict(id=identifier,phase=phase,language=language,kind=kind,path=str(directory),
                    repository_id=f'knowledge-{language}',experience_contract='numeric-v1' if kind=='numeric-v1' else 'stable-v2',
                    source=source,test=test,hidden=str(hidden_path),expected_tests=len(visible),hidden_tests=len(hidden),task_description=detail))
    (root/'catalog.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2),encoding='utf-8')
    return catalog
