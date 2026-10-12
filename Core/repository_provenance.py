"""Read-only source inventory and producing revision; never stage or clean files."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timezone
import ast
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
from urllib.parse import urlsplit, unquote

VERSION = '1.0.0'
FINGERPRINT_VERSION = 'sha256-source-state-v1'
EXPECTED_REMOTE = 'github.com/AMedrano117/M365-Copilot-Readiness-Assessment'
_active = ContextVar('assessment_producing_revision', default=None)
SOURCE_DIRS = {'Core', 'Recommendations', 'tests', 'tools', 'docs'}
SOURCE_SUFFIXES = {'.py', '.ps1', '.md', '.json', '.toml', '.yaml', '.yml', '.txt', '.ini', '.cfg'}
LOCAL_DIRS = {'.venv', 'venv', '.assessment-deps', '.cache', '.pytest_cache', '.kilo', '.vscode', '.idea', '.playwright-cli'}
EVIDENCE_DIRS = {'Reports', 'reports', 'output', 'Output', 'Collections', 'collections', '.private-assessment-evidence'}
ARTIFACT_SUFFIXES = {'.xlsx', '.pdf', '.zip', '.html', '.pptx', '.pfx', '.pem', '.key', '.p12'}
ROOT_SOURCE = {'main.py', 'params.py', 'collector-registry.json', 'requirements.lock.txt', 'requirements.txt',
               '.gitignore', '.gitattributes', '.env.example', 'README.md', 'LICENSE'}
IMPORT_MODULES = ('portal_report_import', 'data_exposure_assessment', 'copilot_readiness_import',
                  'power_platform_inventory', 'prior_report_import', 'purview_cache_import',
                  'portal_review', 'pdf_report_import', 'ai_baseline_import', 'ai_baseline_native')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def normalize_remote(value):
    """Public host/path only; strip userinfo, query, fragment and local paths."""
    raw = str(value or '').strip()
    if '://' not in raw:
        match = re.fullmatch(r'(?:[^/@:]+@)?([A-Za-z0-9.-]+):([^?#]+)(?:[?#].*)?', raw)
        if not match: return None
        raw = 'ssh://' + match[1] + '/' + match[2]
    try:
        parsed = urlsplit(raw)
        if parsed.scheme not in {'http', 'https', 'ssh', 'git'} or not parsed.hostname: return None
        host = parsed.hostname.lower()
        path = unquote(parsed.path).strip('/').removesuffix('.git')
        # Authentication parameters and encoded credential material are never retained.
        if not path or any(c in path for c in '@?#\\\r\n') or re.search(r'(?i)(token|password|secret)=', path): return None
        return host + '/' + path
    except ValueError:
        return None


def parse_status(raw):
    parts = iter(raw.decode('utf-8', 'surrogateescape').split('\0'))
    entries = []
    for item in parts:
        if not item or item.startswith('#'): continue
        kind = item[0]
        if kind in {'?', '!'}:
            entries.append(dict(path=item[2:], status=kind*2, tracked=False, ignored=kind=='!',
                                head_blob=None, index_blob=None))
        elif kind in {'1', '2'}:
            fields = item.split(' ', 8 if kind=='1' else 9)
            entry = dict(path=fields[-1], status=fields[1], tracked=True, ignored=False,
                         head_blob=fields[6], index_blob=fields[7], modes=fields[3:6])
            if kind=='2': entry['original_path'] = next(parts, '')
            entries.append(entry)
        elif kind=='u':
            fields=item.split(' ',10)
            entries.append(dict(path=fields[-1],status=fields[1],tracked=True,ignored=False,
                                unmerged=True,head_blob=None,index_blob=None))
    return sorted(entries, key=lambda row:(row['path'],row.get('original_path','')))


def classify_entry(entry, content=None):
    """Conservative structural classification; no deletion is ever authorized."""
    row = deepcopy(entry)
    path=Path(row['path']); parts=path.parts; suffix=path.suffix.lower()
    source_like = (path.as_posix() in ROOT_SOURCE or
        (parts and parts[0] in SOURCE_DIRS and suffix in SOURCE_SUFFIXES) or
        (len(parts)==1 and suffix in {'.ps1','.py'}) or
        (parts and parts[0]=='.github' and suffix in {'.yml','.yaml'}))
    category='UnknownOrReviewRequired'; labels=['UnsafeToDelete']; relevant=True
    if any(part in EVIDENCE_DIRS for part in parts) or (parts and parts[0]=='examples' and len(parts)>2):
        category='CustomerEvidence'; labels += ['MayContainCustomerData','MayContainPersonalData','UnsafeToCommit']
        relevant=row['tracked'] or not row.get('ignored')
    elif path.name!='.env.example' and ('.env' in path.name or suffix in {'.pfx','.pem','.key','.p12'}):
        category='LocalEnvironment'; labels += ['MayContainSecrets','UnsafeToCommit','LocalOnly']
        relevant=row['tracked'] or not row.get('ignored')
    elif any(part in LOCAL_DIRS for part in parts):
        category='LocalEnvironment'; labels += ['LocalOnly','UnsafeToCommit']
        relevant=row['tracked']
    elif '__pycache__' in parts or suffix in {'.pyc','.pyo'}:
        category='GeneratedArtifact'; labels += ['Generated','Reproducible','UnsafeToCommit']; relevant=row['tracked']
    elif suffix in ARTIFACT_SUFFIXES:
        category='CustomerEvidence'; labels += ['MayContainCustomerData','Generated','UnsafeToCommit']
        relevant=row['tracked'] or not row.get('ignored')
    elif suffix=='.log' or path.name in {'el.open','.coverage'}:
        category='TemporaryArtifact'; labels += ['MayContainSecrets','UnsafeToCommit']; relevant=row['tracked']
    elif source_like:
        valid=True
        if content is not None and suffix in {'.py','.json'}:
            try:
                decoded=content.decode('utf-8-sig')
                ast.parse(decoded) if suffix=='.py' else json.loads(decoded)
            except (ValueError,UnicodeError,SyntaxError): valid=False
        if valid:
            category='PurviewImportDevelopment' if any(word in path.name.lower() for word in
                ('ai_baseline','purview_billing','evidence_requests')) else 'IntendedProductSource'
            labels += ['RequiredForTests' if parts[0]=='tests' else 'DocumentationOnly' if suffix=='.md' else 'RequiredForRuntime']
    if row.get('read_error') or row.get('symlink') or row.get('unmerged'):
        category='UnknownOrReviewRequired'
    secret_types=secret_risk_types(content)
    if secret_types:
        category='UnknownOrReviewRequired'; labels+=['MayContainSecrets','UnsafeToCommit']
    row.update(classification=category, secondary_labels=sorted(set(labels)), relevant_source=relevant,
        source_like=source_like, confidence='Structural classification only',
        disposition='AddToGitignoreAndPreserveLocally' if row.get('ignored') else 'PreserveInPlacePendingReview',
        may_stage=False,may_commit=False,may_delete=False,secret_risk_types=secret_types,
        evidence='Git state, bounded product-path conventions and syntax; preservation is mandatory.')
    return row


def secret_risk_types(content):
    if content is None:return []
    value=content.decode('utf-8','replace')
    patterns={'private_key':r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
              'token':r'\beyJ[A-Za-z0-9_-]{25,}\.[A-Za-z0-9_-]{25,}\.[A-Za-z0-9_-]{25,}',
              'environment_secret':r'(?im)^(?:CLIENT_SECRET|ACCESS_TOKEN|REFRESH_TOKEN|PASSWORD)\s*=\s*[^\s#][^\r\n]{10,}$'}
    return sorted(name for name,pattern in patterns.items() if re.search(pattern,value))


def dirty_fingerprint(entries):
    records=[]
    for row in entries:
        # Untracked evidence/cache bytes are excluded; their risks still affect policy.
        if row.get('ignored') or (not row.get('tracked') and not row.get('source_like')): continue
        records.append({key:row.get(key) for key in
            ('path','original_path','status','head_blob','index_blob','modes','content_sha256','read_error')})
    return digest({'version':FINGERPRINT_VERSION,'entries':sorted(records,key=lambda r:(r['path'],r.get('original_path') or ''))}) if records else None


def _git(root, *args):
    result=subprocess.run(['git','--no-optional-locks',*args],cwd=root,stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,check=False,timeout=30,env={**os.environ,'GIT_OPTIONAL_LOCKS':'0'})
    if result.returncode: raise ValueError('Git metadata unavailable')
    return result.stdout


def _read(root, path):
    target=root/path
    if target.is_symlink() or any(parent.is_symlink() for parent in target.parents if parent!=root and parent.is_relative_to(root)):
        return {'symlink':True},None
    if not target.exists(): return {'content_sha256':None,'deleted':True},None
    try:
        data=target.read_bytes()
        return {'content_sha256':hashlib.sha256(data).hexdigest(),'size':len(data)},data
    except OSError as exc:
        return {'read_error':type(exc).__name__,'content_sha256':None},None


def inventory_repository(root, *, ignored=False, git_reader=None):
    root=Path(root).resolve(); read=git_reader or (lambda *args:_git(root,*args))
    args=['status','--porcelain=v2','-z','--untracked-files=all']
    if ignored: args=['status','--porcelain=v2','-z','--untracked-files=normal','--ignored=matching']
    result=[]
    for row in parse_status(read(*args)):
        path=Path(row['path'])
        if path.is_absolute() or '..' in path.parts:
            row.update(read_error='UnsafeRelativePath'); data=None
        elif row.get('ignored') and (root/path).is_dir(): data=None
        else:
            metadata,data=_read(root,path); row.update(metadata)
        result.append(classify_entry(row,data))
    return result


def runtime_versions(root=None):
    from .cross_provider_assessment import METHODOLOGY_VERSION
    from .identity_finding_taxonomy import VERSION as TAXONOMY_VERSION
    from .assessment_catalog import DOMAIN_SPECS
    from .assessment_identity import digest as identity_digest
    base=Path(root or Path(__file__).resolve().parents[1])
    adapters={}
    for name in IMPORT_MODULES:
        file=base/'Core'/(name+'.py')
        if file.is_file():
            version=None; data=None
            try:
                data=file.read_bytes(); tree=ast.parse(data.decode('utf-8-sig'))
            except (OSError,SyntaxError,UnicodeError):
                adapters[name]={'version':None,'implementation_sha256':hashlib.sha256(data).hexdigest() if data is not None else None}
                continue
            for node in tree.body:
                if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id in {'VERSION','SCHEMA_VERSION'} for t in node.targets):
                    if isinstance(node.value,ast.Constant) and isinstance(node.value.value,(str,int)): version=str(node.value.value)
            adapters[name]={'version':version,'implementation_sha256':hashlib.sha256(data).hexdigest()}
    return dict(methodology_version=METHODOLOGY_VERSION,taxonomy_version=TAXONOMY_VERSION,
        catalog_version='catalog-'+identity_digest(DOMAIN_SPECS),benchmark_catalog_version=None,import_adapters=adapters)


def configuration_hash(config):
    """Portable non-secret options and input selections; package hashes bind bytes."""
    source=vars(config) if hasattr(config,'__dict__') else dict(config or {})
    fields=('mode','run_type','assessment_id','baseline_run_id','assessment_purpose','primary_environment_id',
        'delta_mode','permission_profile','services','preview_collectors','interactive_auth','delegated',
        'legacy_power_platform_collector','include_user_usage_detail','data_exposure_enabled','report_format',
        'evaluation_date','source_policy','source_reason','acknowledge_dirty_source')
    values={key:source[key] for key in fields if key in source}
    for key,value in list(values.items()):
        if isinstance(value,(set,list,tuple)): values[key]=sorted(value)
    inputs=('sam_report','dspm_report','reports_dir','collection_input','assessment_profile','provider_evidence',
        'copilot_readiness_export','copilot_dashboard_export','power_platform_inventory','prior_report','purview_cache',
        'portal_review','baseline','assessment_history','ai_baseline_export')
    values['input_selections']={key:input_selection_count(key,source[key])
                             for key in inputs if source.get(key)}
    return digest(values)


def input_selection_count(role, supplied):
    """Deduplicate copied export bytes without retaining paths or source content."""
    paths=supplied if isinstance(supplied,(list,tuple)) else [supplied]
    if role not in {'sam_report','dspm_report','reports_dir'}:return len(paths)
    suffixes={'.csv','.tsv','.xlsx','.zip'}
    identities=set()
    for value in paths:
        path=Path(value)
        try:candidates=sorted(path.iterdir()) if path.is_dir() else [path]
        except OSError:
            identities.add(('unreadable',path.name));continue
        for candidate in candidates:
            if candidate.is_symlink():
                identities.add(('unreadable',candidate.name));continue
            if path.is_dir() and (not candidate.is_file() or candidate.suffix.lower() not in suffixes):continue
            try:
                identities.add((candidate.suffix.lower(),hashlib.sha256(candidate.read_bytes()).hexdigest()))
            except OSError:identities.add(('unreadable',candidate.name))
    return len(identities)


def capture_revision(root=None, config=None, *, git_reader=None, expected_remote=EXPECTED_REMOTE, generated_at=None):
    root=Path(root or Path(__file__).resolve().parents[1]).resolve()
    read=git_reader or (lambda *args:_git(root,*args))
    result=dict(schema_version=VERSION,repository_identifier=EXPECTED_REMOTE,remote_identifier=None,
        branch=None,commit_sha=None,commit_tree_sha=None,upstream_branch=None,upstream_commit=None,
        ahead_count=None,behind_count=None,source_state='SourceStateUnavailable',staged_change_count=0,
        tracked_modification_count=0,untracked_entry_count=0,dirty_tree_fingerprint=None,
        fingerprint_version=FINGERPRINT_VERSION,run_configuration_hash=configuration_hash(config),
        collector_configuration_hash=None,generated_at=generated_at or datetime.now(timezone.utc).isoformat(),
        execution_environment={'os':platform.system(),'python':platform.python_version()},warnings=[],diagnostics=[])
    result.update(runtime_versions(root))
    options=vars(config) if hasattr(config,'__dict__') else dict(config or {})
    result['collector_configuration_hash']=collector_configuration_hash(options)
    def optional(*args):
        try:return read(*args).decode('utf-8','replace').strip()
        except (OSError,ValueError,RuntimeError,subprocess.SubprocessError):return None
    try:
        result['commit_sha']=read('rev-parse','HEAD').decode().strip()
        result['commit_tree_sha']=read('rev-parse','HEAD^{tree}').decode().strip()
        result['branch']=optional('symbolic-ref','--short','HEAD')
        result['upstream_branch']=optional('rev-parse','--abbrev-ref','--symbolic-full-name','@{upstream}')
        result['upstream_commit']=optional('rev-parse','@{upstream}')
        counts=optional('rev-list','--left-right','--count','HEAD...@{upstream}')
        if counts: result['ahead_count'],result['behind_count']=map(int,counts.split())
        remote=optional('remote','get-url','github') or optional('remote','get-url','origin')
        result['remote_identifier']=normalize_remote(remote)
        entries=inventory_repository(root,git_reader=read)
        result['staged_change_count']=sum(r['tracked'] and r['status'][0] not in {' ','.'} for r in entries)
        result['tracked_modification_count']=sum(r['tracked'] for r in entries)
        result['untracked_entry_count']=sum(not r['tracked'] for r in entries)
        result['dirty_tree_fingerprint']=dirty_fingerprint(entries)
        dangerous=[r for r in entries if r.get('relevant_source') and
                   (r['classification'] not in {'IntendedProductSource','PurviewImportDevelopment'} or r.get('read_error'))]
        dirty=any(r.get('relevant_source') for r in entries)
        result['source_state']='DirtyUnclassified' if dangerous else 'DirtyReproducible' if dirty else 'CleanCommitted'
        if not result['branch']: result['source_state']='DetachedOrUnowned'
        if result['remote_identifier']!=expected_remote:
            result['source_state']='DetachedOrUnowned'
            result['diagnostics'].append({'code':'remote_mismatch','severity':'error'})
        for row in entries:
            if row.get('relevant_source'):
                code=('local_source_not_committed' if row['tracked'] else 'untracked_source_like_file') if row['source_like'] else 'dirty_unclassified_source'
                result['diagnostics'].append({'code':code,'severity':'warning'})
                if row['tracked'] and row['classification'] in {'CustomerEvidence','GeneratedArtifact','LocalEnvironment'}:
                    result['diagnostics'].append({'code':'unsafe_material_in_product_index','severity':'error'})
                if row.get('secret_risk_types'):
                    result['diagnostics'].append({'code':'secret_risk_file_staged' if row['status'][0] not in {'.',' ','?'} else 'secret_risk_source','severity':'error'})
        if dangerous:result['diagnostics'].append({'code':'dirty_unclassified_source','severity':'error'})
        if result['source_state']=='DirtyReproducible' and not result['dirty_tree_fingerprint']:
            result['source_state']='DirtyUnclassified';result['diagnostics'].append({'code':'dirty_fingerprint_failure','severity':'error'})
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError):
        result['source_state']='SourceStateUnavailable';result['diagnostics'].append({'code':'git_metadata_unavailable','severity':'error'})
    if result['source_state']!='CleanCommitted': result['warnings'].append('Producing source is '+result['source_state']+'; customer-ready publication is not eligible.')
    result['qualification']='Committed source' if result['source_state']=='CleanCommitted' else 'Unqualified source for customer publication'
    result['diagnostics']=sorted({json.dumps(d,sort_keys=True):d for d in result['diagnostics']}.values(),key=lambda d:(d['code'],d['severity']))
    return result


def publication_policy(revision, *, policy='production', purpose=None, reason=None, acknowledged=False):
    state=revision.get('source_state'); errors=[]
    if any(secret_risk_types(str(value or '').encode()) or re.search(r'(?i)(?:[a-z]:[\\/]|/(?:home|users)/)',str(value or '')) for value in (purpose,reason)):
        purpose=reason='Redacted unsafe provenance annotation'
        errors.append('unsafe_provenance_annotation')
    if policy=='production':
        if state!='CleanCommitted' or any(not re.fullmatch(r'[0-9a-f]{40,64}',str(revision.get(key) or '')) for key in ('commit_sha','commit_tree_sha')):
            errors.append('production_requires_clean_committed_source')
    elif policy=='engineering-validation':
        if state not in {'CleanCommitted','DirtyReproducible'}: errors.append('engineering_source_unclassified_or_unavailable')
        if not purpose or not reason or acknowledged is not True: errors.append('engineering_requires_purpose_reason_acknowledgement')
        if state=='DirtyReproducible' and not revision.get('dirty_tree_fingerprint'): errors.append('dirty_fingerprint_failure')
    else:errors.append('unknown_source_policy')
    return dict(policy=policy,execution_eligible=not errors,customer_publication_eligible=not errors and policy=='production',
        output_qualification='Customer publication eligible' if not errors and policy=='production' else 'ENGINEERING VALIDATION ONLY — NOT CUSTOMER-READY' if not errors else 'PUBLICATION BLOCKED',
        purpose=purpose,reason=reason,source_state_acknowledged=acknowledged,errors=errors)


def unrecorded_revision():
    return {'schema_version':VERSION,'source_state':'SourceStateUnavailable','commit_sha':None,'commit_tree_sha':None,
        'qualification':'Programmatic or historical provenance not recorded; reproducibility unverified',
        'warnings':['Producing revision was not recorded. No current-checkout attribution is inferred.']}


def current_revision(): return deepcopy(_active.get() or unrecorded_revision())


@contextmanager
def producing_context(revision):
    token=_active.set(deepcopy(revision))
    try:yield
    finally:_active.reset(token)


def set_execution_revision(revision):
    """CLI execution context; library unit tests never query Git implicitly."""
    _active.set(deepcopy(revision))


def collector_configuration_hash(options):
    values={key:options.get(key) for key in
        ('services','preview_collectors','permission_profile','legacy_power_platform_collector','include_user_usage_detail')}
    if isinstance(values['services'],(list,tuple,set)):values['services']=sorted(values['services'])
    return digest(values)


def update_execution_configuration(args):
    """Record effective restored options without another Git query."""
    revision=_active.get()
    if revision is None:return
    revision=deepcopy(revision)
    revision['run_configuration_hash']=configuration_hash(args)
    revision['collector_configuration_hash']=collector_configuration_hash(vars(args))
    _active.set(revision)


def require_execution(revision):
    policy=revision.get('publication') or {}
    if not policy.get('execution_eligible'):
        raise ValueError('Source publication gate blocked execution: '+', '.join(policy.get('errors') or ['producing_revision_missing']))


def verify_execution_source():
    original=_active.get()
    if not original or not original.get('publication'):return
    require_execution(original)
    current=capture_revision()
    fields=('commit_sha','commit_tree_sha','dirty_tree_fingerprint','source_state','methodology_version','taxonomy_version','import_adapters')
    if any(original.get(key)!=current.get(key) for key in fields):
        raise ValueError('Source publication gate blocked: producing source changed after preflight.')


def provenance_html(result):
    from html import escape
    record=result.get('producing_revision') or unrecorded_revision()
    qualification=(record.get('publication') or {}).get('output_qualification') or record.get('qualification')
    tool=result.get('replay_tool_revision') or {}
    replay_note=''
    if tool:
        tool_qualification=(tool.get('publication') or {}).get('output_qualification') or tool.get('qualification') or 'Replay provenance unverified'
        replay_note='<p>'+escape('Replay tool: '+str(tool.get('source_state'))+'; commit '+str(tool.get('commit_sha') or 'unrecorded')+'. '+str(tool_qualification))+'</p>'
    return '<section class="qualification" id="source-provenance"><h2>Producing source</h2><p>'+escape(str(qualification))+'</p><p>'+escape(
        str(record.get('source_state'))+'; commit '+str(record.get('commit_sha') or 'unrecorded'))+'</p>'+replay_note+'</section>'


def provenance_rows(result):
    revision=result.get('producing_revision') or unrecorded_revision()
    rows=[{'Item':'Producing '+key,'Value':revision.get(key)} for key in
        ('commit_sha','commit_tree_sha','branch','source_state','dirty_tree_fingerprint','qualification')] + [
        {'Item':'Publication qualification','Value':(revision.get('publication') or {}).get('output_qualification') or revision['qualification']}]
    tool=result.get('replay_tool_revision')
    if tool:
        rows.extend({'Item':'Replay tool '+key,'Value':tool.get(key)} for key in ('commit_sha','source_state','qualification'))
    return rows
