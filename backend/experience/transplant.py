"""Code-target location and splice shared by cross-verification paths.

Only a uniquely locatable target with an identical declaration may be grafted.
Unsupported languages, missing grammars and ambiguous targets raise
VerificationUnavailable: a failed lookup never means "the rules do not conflict".
Python keeps the original AST behaviour; other languages use tree-sitter and
replace only the function body, so the target signature stays untouched.
"""
import ast
import builtins
import copy

from .automatic_resolution import VerificationUnavailable

SUPPORTED = {'python', 'java', 'go', 'cpp'}
EXTENSIONS = {
    'python': ('.py',),
    'java': ('.java',),
    'go': ('.go',),
    'cpp': ('.cpp', '.cc', '.cxx', '.hpp', '.h', '.hh'),
}
GRAMMARS = {
    'java': ('tree_sitter_java', {'method_declaration', 'constructor_declaration'}),
    'go': ('tree_sitter_go', {'function_declaration', 'method_declaration'}),
    'cpp': ('tree_sitter_cpp', {'function_definition'}),
}
TYPE_NODES = {'class_declaration', 'interface_declaration', 'enum_declaration', 'record_declaration',
              'class_specifier', 'struct_specifier'}


def supports(language):
    return language in SUPPORTED


def accepts_path(language, relative):
    return relative.endswith(EXTENSIONS.get(language, ()))


def changed(before_text, after_text, language, qualified):
    """True when the named target exists in both versions with a different body."""
    return declaration(before_text, language, qualified) != declaration(after_text, language, qualified)


def transplant(target_text, donor_text, language, qualified):
    if language == 'python':
        return _transplant_python(target_text, donor_text, qualified)
    if not supports(language):
        raise VerificationUnavailable('该语言暂不支持自动交叉验证。')
    target = _locate(target_text, language, qualified)
    donor = _locate(donor_text, language, qualified)
    if target.parent is not None and target.parent.type == 'template_declaration':
        raise VerificationUnavailable('模板函数暂不支持自动交叉验证。')
    if _declaration(target_text, target) != _declaration(donor_text, donor):
        raise VerificationUnavailable('函数声明或作用域不同，不能可靠移植。')
    target_body, donor_body = target.child_by_field_name('body'), donor.child_by_field_name('body')
    if target_body is None or donor_body is None:
        raise VerificationUnavailable('目标没有可移植的函数体。')
    if target.start_point.column != donor.start_point.column:
        raise VerificationUnavailable('函数所在作用域不同，不能可靠移植。')
    if target_text[target_body.start_byte:target_body.end_byte] == donor_text[donor_body.start_byte:donor_body.end_byte]:
        raise VerificationUnavailable('双方真实函数实现相同，无法用此测试区分措辞冲突。')
    return (target_text[:target_body.start_byte] + donor_text[donor_body.start_byte:donor.end_byte]
            + target_text[target.end_byte:])


def declaration(text, language, qualified):
    """Escaped declaration text (signature plus body) used to detect real edits."""
    if language == 'python':
        return body_identity(function(ast.parse(text), qualified))
    return _declaration(text, _locate(text, language, qualified))


# --- Python (unchanged AST behaviour) -------------------------------------------------

def function(tree, qualified):
    nodes = [tree]
    for name in qualified.split('.'):
        nodes = [child for parent in nodes for child in getattr(parent, 'body', [])
                 if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == name]
    if len(nodes) != 1 or not isinstance(nodes[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
        raise VerificationUnavailable('无法唯一定位双方相同的函数。')
    return nodes[0]


def signature(node):
    args = copy.deepcopy(node.args)
    for value in ast.walk(args):
        if isinstance(value, ast.arg):
            value.annotation = None
            value.type_comment = None
    return type(node).__name__, ast.dump(args), [ast.dump(v) for v in node.decorator_list]


def body_identity(node):
    body = node.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    return ast.dump(ast.Module(body=body, type_ignores=[]))


def _transplant_python(target_text, donor_text, qualified):
    target_tree, donor_tree = ast.parse(target_text), ast.parse(donor_text)
    target, donor = function(target_tree, qualified), function(donor_tree, qualified)
    if signature(target) != signature(donor):
        raise VerificationUnavailable('函数调用接口或装饰器不同，不能可靠移植。')
    if body_identity(target) == body_identity(donor):
        raise VerificationUnavailable('双方真实函数实现相同，无法用此测试区分措辞冲突。')
    # Direct helper calls must retain their implementation. Incompatibility is
    # not allowed to masquerade as a failure of the competing repair rule.
    local = {n.arg for n in ast.walk(donor.args) if isinstance(n, ast.arg)}
    local.update(n.id for n in ast.walk(donor) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store))
    for call in [n for n in ast.walk(donor) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]:
        name = call.func.id
        if name in {'eval', 'exec', 'globals', 'locals', '__import__'}:
            raise VerificationUnavailable('动态函数依赖不支持自动交叉验证。')
        if name in local or hasattr(builtins, name):
            continue
        try:
            left, right = function(target_tree, name), function(donor_tree, name)
        except VerificationUnavailable:
            raise VerificationUnavailable('存在无法核对的外部函数依赖。') from None
        if signature(left) != signature(right) or body_identity(left) != body_identity(right):
            raise VerificationUnavailable('双方依赖的辅助函数不同，无法隔离单条规则的作用。')
    target_lines, donor_lines = target_text.splitlines(keepends=True), donor_text.splitlines(keepends=True)
    a, b = target.body[0].lineno-1, donor.body[0].lineno-1
    if target.body[0].col_offset != donor.body[0].col_offset:
        raise VerificationUnavailable('函数所在作用域不同，无法可靠移植。')
    output = ''.join(target_lines[:a] + donor_lines[b:donor.end_lineno] + target_lines[target.end_lineno:])
    ast.parse(output)
    return output


# --- tree-sitter languages ------------------------------------------------------------

def _parser(language):
    try:
        import tree_sitter
        grammar = __import__(GRAMMARS[language][0])
    except ImportError:
        raise VerificationUnavailable('该语言的语法解析组件未安装，暂不能自动交叉验证。') from None
    return tree_sitter.Parser(tree_sitter.Language(grammar.language()))


def _text(text, node):
    return text[node.start_byte:node.end_byte]


def _name(node):
    child = node.child_by_field_name('name')
    if child is not None:
        return child.text.decode('utf-8')
    # C++ declarations carry the name inside nested declarator nodes.
    declarator = node.child_by_field_name('declarator')
    while declarator is not None:
        if declarator.type in {'identifier', 'field_identifier', 'operator_name', 'destructor_name'}:
            return declarator.text.decode('utf-8')
        declarator = declarator.child_by_field_name('declarator')
    return None


def _declaration(text, node):
    body = node.child_by_field_name('body')
    end = body.start_byte if body is not None else node.end_byte
    return ' '.join(text[node.start_byte:end].split())


def _enclosing(node):
    names, parent = [], node.parent
    while parent is not None:
        if parent.type in TYPE_NODES:
            name = _name(parent)
            if name:
                names.append(name)
        parent = parent.parent
    return list(reversed(names))


def _go_receiver(node):
    receiver = node.child_by_field_name('receiver')
    if receiver is None:
        return None
    stack, found = [receiver], None
    while stack:
        current = stack.pop(0)
        if current.type in {'type_identifier', 'identifier'}:
            found = current.text.decode('utf-8')
        stack.extend(current.named_children)
    return found


def _qualified(node, language):
    parts = _enclosing(node)
    name = _name(node)
    if language == 'go' and node.type == 'method_declaration':
        receiver = _go_receiver(node)
        if receiver:
            parts = [*parts, receiver]
    return '.'.join([*parts, name]) if name else None


def qualified_names(text, language):
    """Qualified names of every transplantable target; empty set when unparseable."""
    if language not in GRAMMARS:
        return set()
    tree = _parser(language).parse(text.encode('utf-8'))
    if tree.root_node.has_error:
        return set()
    kinds = GRAMMARS[language][1]
    names, stack = set(), [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type in kinds:
            name = _qualified(node, language)
            if name:
                names.add(name)
        stack.extend(node.named_children)
    return names


def _locate(text, language, qualified):
    import tree_sitter
    kinds = GRAMMARS[language][1]
    tree = _parser(language).parse(text.encode('utf-8'))
    if tree.root_node.has_error:
        raise VerificationUnavailable('目标文件无法解析，不能可靠定位函数。')
    found = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type in kinds and _qualified(node, language) == qualified:
            found.append(node)
        stack.extend(node.named_children)
    if len(found) != 1:
        raise VerificationUnavailable('无法唯一定位双方相同的函数。')
    return found[0]
