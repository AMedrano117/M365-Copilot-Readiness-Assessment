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
    if env_file:
        return os.path.abspath(env_file)

    if base_path is None:
        base_path = os.path.dirname(os.path.dirname(__file__))

    return os.path.join(base_path, '.env')


def load_env_file(env_file=None, base_path=None):
    """Load an environment file if it exists.
    
    Args:
        env_file: Optional explicit environment file path.
        base_path: Base path to look for default .env file. If None, uses parent of Core folder.
    """
    env_path = resolve_env_file_path(env_file=env_file, base_path=base_path)
    os.environ['ASSESSMENT_ENV_FILE'] = env_path

    if os.path.exists(env_path):
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
                    os.environ[key] = value
                    _ENV_FILE_SOURCES[key] = env_path
        return env_path

    return None


def check_credentials(env_file=None):
    """Check if required environment variables are set.
    
    Returns:
        list: List of missing variable names, empty if all present.
    """
    # Load .env file first
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


def validate_credentials_or_exit(get_timestamp_func, env_file=None):
    """Validate credentials and exit with helpful message if missing.
    
    Args:
        get_timestamp_func: Function to get formatted timestamp for messages.
    """
    missing_vars = check_credentials(env_file=env_file)
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
        print("See RUN.md for detailed setup instructions.")
        sys.exit(1)
