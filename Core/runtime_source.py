"""Bind loadable product files to Git blobs independently of status/ignore rules."""
import hashlib
import ast
import os
import dataclasses
import sys
import types
from pathlib import Path

ROOTS = ('Core', 'Recommendations', 'tools', 'tests', 'docs', '.github')
SUFFIXES = {'.py', '.ps1', '.psm1', '.psd1', '.pyd', '.so', '.dll', '.json', '.toml', '.yml', '.yaml', '.ini', '.cfg', '.md', '.txt'}
EXCLUDED = {'__pycache__', '.venv', 'venv', '.cache', '.assessment-deps', '.pytest_cache',
            'Reports','reports','output','Output','Collections','collections','.private-assessment-evidence'}


class MetadataReader:
    """Reuse only Git metadata whose controlling files have unchanged bytes.

    HEAD/tree and status are always queried. Product bytes and loaded bindings
    are still audited on every publication; this cache never trusts file times.
    """
    def __init__(self,root,reader):
        self.root=Path(root);self.reader=reader;self.answers={};self.previous=None
        paths=reader('rev-parse','--git-dir','--git-common-dir').decode('utf-8').splitlines()
        if len(paths)!=2:raise ValueError('Cannot resolve Git metadata directories.')
        self.directories=[(self.root/Path(value)).resolve() for value in paths]

    def refresh(self):
        paths=set()
        if (self.root/'.git').is_file():paths.add(self.root/'.git')
        if os.environ.get('GIT_INDEX_FILE'):paths.add(Path(os.environ['GIT_INDEX_FILE']).absolute())
        for directory in self.directories:
            paths.update(directory/name for name in ('HEAD','index','config','config.worktree','packed-refs','commondir'))
            refs=directory/'refs'
            if refs.is_dir():paths.update(file for file in refs.rglob('*') if file.is_file())
        signatures=[(str(file),hashlib.sha256(file.read_bytes()).hexdigest() if file.exists() else None) for file in sorted(paths)]
        signatures.append(('git_environment',tuple(sorted((key,value) for key,value in os.environ.items() if key.startswith('GIT_')))))
        if signatures!=self.previous:
            if self.previous is not None:
                directories=[(self.root/Path(value)).resolve() for value in self.reader('rev-parse','--git-dir','--git-common-dir').decode('utf-8').splitlines()]
                if len(directories)!=2:raise ValueError('Cannot resolve Git metadata directories.')
                if directories!=self.directories:
                    self.directories=directories;self.previous=None;self.answers.clear();return self.refresh()
            self.answers.clear();self.previous=signatures

    def __call__(self,*args):
        if args[0] in {'status','remote'} or args in {('rev-parse','HEAD'),('rev-parse','HEAD^{tree}')}:return self.reader(*args)
        if args not in self.answers:self.answers[args]=self.reader(*args)
        return self.answers[args]


def audit_source(root, read_git, read_file, existing):
    from .publication_io import linked_path
    tracked = {}
    for record in read_git('ls-files', '--stage', '-z').decode('utf-8', 'surrogateescape').split('\0'):
        if not record:
            continue
        metadata, path = record.split('\t', 1)
        mode, blob, stage = metadata.split()
        tracked[path] = (mode, blob, stage)
    flags = {}
    for record in read_git('ls-files', '-v', '-z').decode('utf-8', 'surrogateescape').split('\0'):
        if record:
            flags[record[2:]] = record[0]
    candidates = set(path for path in tracked if relevant(path))
    errors = {}
    def walk(folder):
        try:
            with os.scandir(folder) as entries:
                for item in entries:
                    relative = Path(item.path).relative_to(root).as_posix()
                    if item.name in EXCLUDED:
                        continue
                    if linked_path(item.path):
                        if relevant(relative) or item.is_dir():
                            errors[relative] = 'SymlinkedSource'
                        continue
                    if item.is_dir(follow_symlinks=False):
                        if (Path(item.path) / '.git').exists():
                            errors[relative] = 'NestedSourceRepository'
                        else:
                            walk(Path(item.path))
                    elif relevant(relative):
                        candidates.add(relative)
        except OSError:
            errors[folder.relative_to(root).as_posix()] = 'InaccessibleSourceDirectory'
    for name in ROOTS:
        folder = root / name
        if linked_path(folder):
            errors[name] = 'SymlinkedSourceDirectory'
        elif folder.is_dir():
            walk(folder)
    for file in root.iterdir():
        if file.suffix.lower() in {'.py', '.ps1'} or file.name in {'.gitignore', '.gitattributes', '.env.example', 'requirements.txt', 'requirements.lock.txt', 'collector-registry.json'}:
            candidates.add(file.name)
    if root == Path(__file__).resolve().parents[1]:
        errors.update(binding_errors(root))
    result = {row['path']: row for row in existing}
    for path in sorted(candidates | set(errors)):
        metadata, content = read_file(root, path)
        indexed = tracked.get(path)
        flag = flags.get(path, '')
        error = errors.get(path)
        if indexed and (indexed[0] == '160000' or indexed[2] != '0'):
            error = 'UnsupportedSourceIndexEntry'
        if indexed and (flag.islower() or flag.upper() == 'S'):
            error = 'HiddenSourceIndexFlag'
        if indexed and content is not None:
            def blob_hash(data):
                algorithm = 'sha256' if len(indexed[1]) == 64 else 'sha1'
                return hashlib.new(algorithm, b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
            # Git's text checkout conversion is equivalent to its committed LF blob.
            matches = indexed[1] in {blob_hash(content), blob_hash(content.replace(b'\r\n', b'\n'))}
            if os.name != 'nt' and metadata.get('executable_mode') != indexed[0]:
                matches = False
        else:
            matches = False
        if path not in result and indexed and matches and not error and not metadata.get('symlink'):
            continue
        row = result.get(path, dict(path=path, tracked=bool(indexed), ignored=False,
            status='.M' if indexed else '??', head_blob=indexed[1] if indexed else None,
            index_blob=indexed[1] if indexed else None, modes=[indexed[0]]*3 if indexed else None))
        row.update(metadata, ignored=False)
        if error:
            row['read_error'] = error
        result[path] = row
    return list(result.values())


def relevant(path):
    parts = Path(path).parts
    return (bool(parts) and parts[0] in ROOTS and Path(path).suffix.lower() in SUFFIXES
            and not any(part in EXCLUDED for part in parts))


def binding_errors(root, modules=None):
    """Detect foreign product modules and stale code loaded before source changed."""
    errors = {}
    for name,module in list((modules if modules is not None else sys.modules).items()):
        declared_product=name in {'Core','Recommendations','main','params'} or name.startswith(('Core.','Recommendations.'))
        product=declared_product
        file=getattr(module,'__file__',None)
        if file and Path(file).resolve().is_relative_to(root):
            relative=Path(file).resolve().relative_to(root)
            if not declared_product and relative.parts[0] in {'.venv','venv','.assessment-deps'}:continue
            if Path(file).suffix.lower() in {'.py','.pyc','.pyo','.pyd','.so','.dll'}:product=True
        if name=='__main__' and getattr(module,'__file__',None):
            product=Path(module.__file__).resolve().is_relative_to(root) and Path(module.__file__).suffix=='.py'
        if not product or module is None:
            continue
        if not file:
            continue
        expected = name.replace('.','/') + '.py'
        try:
            path = Path(file).resolve()
            if not path.is_relative_to(root):
                errors[expected]='RuntimeSourceOutsideRepository';continue
            relative=path.relative_to(root)
            canonical={root/Path(*name.split('.')).with_suffix('.py'),root/Path(*name.split('.'))/'__init__.py'}
            if (declared_product and path not in canonical) or any(part in EXCLUDED for part in relative.parts):
                errors[expected]='RuntimeSourcePathMismatch';continue
            if path.suffix != '.py':
                errors[expected]='UnsupportedLoadedSource';continue
            data=path.read_bytes()
            tree=ast.parse(data)
            mutable_globals={variable for node in ast.walk(tree) if isinstance(node,ast.Global) for variable in node.names}
            declared_functions={node.name for node in tree.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))}
            for function_name in declared_functions:
                if getattr(vars(module).get(function_name),'__module__',None)!=name:
                    errors[relative.as_posix()]='RuntimeFunctionBindingChanged'
            constants={}
            for node in tree.body:
                if isinstance(node,ast.Assign):
                    try:literal=ast.literal_eval(node.value)
                    except (ValueError,TypeError,SyntaxError):continue
                    if isinstance(literal,(str,int,float,bool)) or literal is None:
                        for target in node.targets:
                            if isinstance(target,ast.Name) and target.id.isupper() and target.id not in mutable_globals:constants[target.id]=literal
                elif isinstance(node,ast.AugAssign) and isinstance(node.target,ast.Name) and node.target.id in constants:
                    try:
                        if not isinstance(node.op,ast.Add):raise ValueError
                        constants[node.target.id]+=ast.literal_eval(node.value)
                    except (ValueError,TypeError,SyntaxError):constants.pop(node.target.id,None)
                elif isinstance(node,ast.ClassDef):
                    cls=vars(module).get(node.name)
                    if not isinstance(cls,type) or cls.__module__!=name:
                        errors[relative.as_posix()]='RuntimeClassBindingChanged';continue
                    for method in node.body:
                        if not isinstance(method,(ast.FunctionDef,ast.AsyncFunctionDef)):continue
                        obj=vars(cls).get(method.name)
                        if isinstance(obj,(staticmethod,classmethod)):obj=obj.__func__
                        if isinstance(obj,property):obj=obj.fget
                        if getattr(obj,'__module__',None)!=name:errors[relative.as_posix()]='RuntimeFunctionBindingChanged'
            if any(vars(module).get(key)!=literal for key,literal in constants.items()):
                errors[relative.as_posix()]='RuntimeConstantDiffersFromSource'
            compiled=compile(data,str(file),'exec',dont_inherit=True,optimize=sys.flags.optimize)
            codes={}
            def collect(code):
                codes[(code.co_name,code.co_firstlineno)]=code
                for child in code.co_consts:
                    if isinstance(child,types.CodeType):collect(child)
            collect(compiled)
            objects=list(vars(module).values())
            generated=set()
            for obj in list(objects):
                if isinstance(obj,type) and obj.__module__==name:
                    objects.extend(vars(obj).values())
                    for value in vars(obj).values():
                        if isinstance(value,property):objects.extend(function for function in (value.fget,value.fset,value.fdel) if function is not None)
                    if dataclasses.is_dataclass(obj):
                        generated.update(id(value) for key,value in vars(obj).items()
                            if key in {'__init__','__repr__','__eq__','__lt__','__le__','__gt__','__ge__','__hash__','__setattr__','__delattr__'}
                            and isinstance(value,types.FunctionType) and (value.__code__.co_filename=='<string>'
                                or getattr(getattr(getattr(value,'__wrapped__',None),'__code__',None),'co_filename',None)=='<string>'))
            for obj in objects:
                if isinstance(obj,(staticmethod,classmethod)):obj=obj.__func__
                if getattr(obj,'__module__',None)!=name:continue
                if id(obj) in generated:continue
                while isinstance(obj,types.FunctionType) and hasattr(obj,'__wrapped__'):
                    if obj.__code__.co_filename==str(file) and obj.__code__!=codes.get((obj.__code__.co_name,obj.__code__.co_firstlineno)):
                        errors[relative.as_posix()]='RuntimeCodeDiffersFromSource'
                    obj=obj.__wrapped__
                if not isinstance(obj,types.FunctionType):continue
                actual=obj.__code__;declared=codes.get((actual.co_name,actual.co_firstlineno))
                if declared is None or actual!=declared:
                    errors[path.relative_to(root).as_posix()]='RuntimeCodeDiffersFromSource';break
        except (OSError,ValueError,SyntaxError,TypeError):
            errors[expected]='RuntimeSourceUnverifiable'
    return errors
