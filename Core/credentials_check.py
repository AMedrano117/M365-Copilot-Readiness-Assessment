"""
Credential validation for Azure/Microsoft 365 authentication.
Checks for required environment variables before starting orchestration.
"""
import os
import re
import sys


_ENV_FILE_SOURCES = {}


def env_variable_source(name):
    """Describe configuration provenance without exposing any credential value."""
    path = _ENV_FILE_SOURCES.get(name)
    return f'{name} in {path}' if path else f'{name} environment variable'


def resolve_env_file_path(env_file=None, base_path=None):
    """Resolve the environment file path to use for this run."""
    if env_file is not None:
        return os.path.abspath(env_file)

    if base_path is None:
        base_path = os.path.dirname(os.path.dirname(__file__))

    return os.path.join(base_path, '.env')


def load_env_file(env_file=None, base_path=None):
    """Load configuration atomically; an explicitly selected file must exist.
    
    Args:
        env_file: Optional explicit environment file path.
        base_path: Base path to look for default .env file. If None, uses parent of Core folder.
    """
    env_path = resolve_env_file_path(env_file=env_file, base_path=base_path)
    if not os.path.isfile(env_path):
        if env_file is not None or os.path.exists(env_path):
            raise ValueError(f'Environment file does not exist or is not a file: {env_path}')
        os.environ['ASSESSMENT_ENV_FILE'] = env_path
        return None

    values = {}
    try:
        with open(env_path, 'r', encoding='utf-8-sig') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip().removeprefix('export ').strip()
                    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
                        continue
                    value = value.strip()
                    if value.startswith(('"', "'")):
                        # Preserve literal secrets and Windows paths; never expand
                        # variables, commands, or backslash escapes in .env values.
                        quoted = re.fullmatch(r'''(["'])(.*?)\1\s*(?:#.*)?''', value)
                        if not quoted:
                            raise ValueError(f'Invalid quoted value for {key} in {env_path}')
                        value = quoted.group(2)
                    else:
                        value = re.split(r'\s+#', value, maxsplit=1)[0].rstrip()
                    if '\x00' in value:
                        raise ValueError(f'Invalid value for {key} in {env_path}')
                    values[key] = value
    except (OSError, UnicodeError) as exc:
        raise ValueError(f'Environment file cannot be read: {env_path}') from exc
    os.environ.update(values)
    os.environ['ASSESSMENT_ENV_FILE'] = env_path
    _ENV_FILE_SOURCES.update({key: env_path for key in values})
    return env_path


def check_credentials(env_file=None, *, load_environment=True):
    """Check if required environment variables are set.
    
    Returns:
        list: List of missing variable names, empty if all present.
    """
    # Load .env file first
    if load_environment:
        load_env_file(env_file=env_file)
    
    # Check required variables
    missing = []
    if not os.environ.get('TENANT_ID'):
        missing.append('TENANT_ID')
    if not os.environ.get('CLIENT_ID'):
        missing.append('CLIENT_ID')

    # Either auth method is acceptable. Certificate auth is preferred because it avoids
    # keeping a long-lived secret in a plaintext file.
    if not (os.environ.get('CERTIFICATE_PATH') or os.environ.get('CLIENT_SECRET')):
        missing.append('CERTIFICATE_PATH or CLIENT_SECRET')

    return missing


def validate_credentials_or_exit(get_timestamp_func, env_file=None, *, load_environment=True):
    """Validate credentials and exit with helpful message if missing.
    
    Args:
        get_timestamp_func: Function to get formatted timestamp for messages.
    """
    missing_vars = check_credentials(env_file=env_file, load_environment=load_environment)
    if missing_vars:
        print(f"[{get_timestamp_func()}] ❌ Missing required credentials: {', '.join(missing_vars)}")
        print()
        print("To use this tool, you need to configure Azure credentials:")
        print("  1. Run: .\\setup-service-principal.ps1")
        print("  2. Or create an environment file with TENANT_ID, CLIENT_ID, and either")
        print("     CERTIFICATE_PATH (plus CERTIFICATE_PASSWORD if the file is protected)")
        print("     or CLIENT_SECRET")
        if env_file:
            print(f"  3. Requested environment file: {resolve_env_file_path(env_file)}")
        print()
        print("See docs/RUN.md for detailed setup instructions.")
        sys.exit(1)


def print_configuration_summary(*, tenant_id, permission_profile, env_path=None,
                                sharepoint_admin_url=None):
    """Show the effective connection targets without printing credential values."""
    from .console_reporting import status
    from .sharepoint_configuration import is_valid_sharepoint_admin_url

    status(f'Configuration: {env_path or "process environment (no default .env file)"}.')
    status(f'Tenant: {tenant_id or "not configured"}.')
    status(f'Application: {os.environ.get("CLIENT_ID") or "not configured"}.')
    status(f'Permission profile: {permission_profile}.')
    method = 'certificate' if os.environ.get('CERTIFICATE_PATH') else 'client secret'
    status(f'Graph authentication: {method}.')
    if permission_profile == 'restricted':
        status('SharePoint administration: not requested by Restricted profile.')
        return
    url = sharepoint_admin_url if sharepoint_admin_url is not None else os.environ.get('SHAREPOINT_ADMIN_URL', '')
    if is_valid_sharepoint_admin_url(url):
        status(f'SharePoint admin URL: {url.strip().rstrip("/")} (connection not yet verified).')
    elif url:
        status('SharePoint admin URL: invalid; correct the configured HTTPS origin.', 'warning')
    else:
        status('SharePoint admin URL: not configured; selected interactive collection may request it.')
