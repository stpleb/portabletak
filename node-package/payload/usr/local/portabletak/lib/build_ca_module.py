"""Build a generic-only CA module from reviewed OTS 1.7.13 source.

Never modifies the source module or the installed ASGARD environment.
"""
import ast
import hashlib
from pathlib import Path

HELPER = '''
import shlex as _pt_shlex
import secrets as _pt_secrets
from xml.sax.saxutils import escape as _pt_xml_escape

def _pt_arg(value):
    return _pt_shlex.quote(str(value))

def _pt_openssl(command):
    args = _pt_shlex.split(command)
    cwd = None
    if args[:1] == ["cd"]:
        if len(args) < 4 or args[2] != "&&":
            raise RuntimeError("invalid_crypto_command")
        cwd, args = args[1], args[3:]
    if not args or args[0] != "openssl":
        raise RuntimeError("invalid_crypto_command")
    environment = os.environ.copy()
    for index, arg in enumerate(args):
        if index and args[index - 1] in ("-passin", "-passout"):
            if not arg.startswith("pass:"):
                raise RuntimeError("invalid_crypto_password_source")
            variable = "PORTABLETAK_CRYPTO_" + str(index)
            environment[variable] = arg[5:]
            args[index] = "env:" + variable
    result = subprocess.run(args, cwd=cwd, env=environment,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=90, check=False)
    return result.returncode
'''

class GenericCA(ast.NodeTransformer):
    def __init__(self):
        self.function = ''
        self.commands = 0
        self.calls = 0
        self.exports = 0
        self.preferences = 0
        self.truststores = 0

    def visit_FunctionDef(self, node):
        previous, self.function = self.function, node.name
        self.generic_visit(node)
        if any(arg.arg == 'common_name' for arg in node.args.args):
            guard = ast.parse("if not isinstance(common_name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}', common_name):\n    raise ValueError('invalid_certificate_name')").body
            node.body[0:0] = guard
        self.function = previous
        return node

    def visit_Assign(self, node):
        self.generic_visit(node)
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'command':
            call = node.value
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute) or call.func.attr != 'format' or not isinstance(call.func.value, ast.Constant):
                raise ValueError('unexpected command source')
            if call.func.value.value.startswith('openssl pkcs12') and '-nokeys' in call.func.value.value:
                call.args[-2] = ast.parse('self.app.config["PORTABLETAK_TRUSTSTORE_PASSWORD"]', mode='eval').body
                self.truststores += 1
            if call.func.value.value.startswith('openssl pkcs12') and '-inkey' in call.func.value.value:
                # Native last argument encrypts the exported client/server p12.
                call.args[-1] = ast.parse('self.app.config["PORTABLETAK_CLIENT_PASSWORD"]', mode='eval').body
                self.exports += 1
            call.args = [ast.Call(func=ast.Name(id='_pt_arg', ctx=ast.Load()), args=[arg], keywords=[]) for arg in call.args]
            self.commands += 1
        return node

    def visit_Expr(self, node):
        if isinstance(node.value, ast.Call):
            call = node.value
            if isinstance(call.func, ast.Attribute) and call.func.attr == 'debug' and call.args and isinstance(call.args[0], ast.Name) and call.args[0].id == 'command':
                return ast.copy_location(ast.Pass(), node)
        return self.generic_visit(node)

    def visit_Call(self, node):
        self.generic_visit(node)
        if isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == 'subprocess' and node.func.attr == 'call':
                self.calls += 1
                return ast.copy_location(ast.Call(func=ast.Name(id='_pt_openssl', ctx=ast.Load()), args=node.args, keywords=[]), node)
            if isinstance(node.func.value, ast.Name) and node.func.value.id == 'random' and node.func.attr == 'randint':
                return ast.parse('_pt_secrets.randbits(159) or 1', mode='eval').body
            if self.function == 'sign_csr' and node.func.attr == 'render':
                for keyword in node.keywords:
                    if keyword.arg == 'alt_name_field':
                        keyword.value = ast.Constant(value='IP.1')
                    elif keyword.arg == 'common_name':
                        keyword.value = ast.parse('self.app.config["OTS_SERVER_ADDRESS"]', mode='eval').body
            if self.function == 'generate_zip' and node.func.attr == 'render':
                for keyword in node.keywords:
                    if keyword.arg == 'server':
                        keyword.value = ast.parse('self.app.config["OTS_SERVER_ADDRESS"]', mode='eval').body
                    if keyword.arg == 'cert_password':
                        keyword.value = ast.parse('self.app.config["PORTABLETAK_CLIENT_PASSWORD"]', mode='eval').body
                        self.preferences += 1
                    if keyword.arg in ('ca_password', 'cert_password'):
                        keyword.value = ast.Call(func=ast.Name(id='_pt_xml_escape', ctx=ast.Load()), args=[keyword.value], keywords=[])
        return node


def build(source, target, expected_sha256):
    raw = Path(source).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('OTS CA source differs from reviewed source')
    tree = ast.parse(raw.decode())
    patch = GenericCA()
    tree = patch.visit(tree)
    if (patch.commands, patch.calls, patch.exports, patch.preferences, patch.truststores) != (11, 11, 2, 2, 2):
        raise ValueError('unexpected native CA structure: ' + repr((patch.commands, patch.calls, patch.exports, patch.preferences, patch.truststores)))
    tree.body[0:0] = ast.parse(HELPER).body
    ast.fix_missing_locations(tree)
    output = '# Generated from reviewed OTS source; generic runtime only.\n' + ast.unparse(tree) + '\n'
    compile(output, str(target), 'exec')
    if Path(target).exists():
        raise ValueError('target already exists')
    Path(target).write_text(output)
