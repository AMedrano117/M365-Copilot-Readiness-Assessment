from azure.identity import CertificateCredential, ClientSecretCredential
from msgraph import GraphServiceClient
import httpx
import logging
import os

# Suppress Azure SDK warnings
logging.getLogger('azure.identity').setLevel(logging.ERROR)

# Load environment file into environment variables (no external dependency)
def _load_env(env_path=None):
    """Load the selected environment file if it exists."""
    if env_path is None:
        env_path = os.getenv('ASSESSMENT_ENV_FILE') or os.path.join(os.path.dirname(__file__), '..', '.env')
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip()


def _ensure_env_loaded():
    """Load credentials from the selected environment file when needed."""
    has_secret = bool(os.getenv('CLIENT_SECRET'))
    has_certificate = bool(os.getenv('CERTIFICATE_PATH'))
    if not (os.getenv('TENANT_ID') and os.getenv('CLIENT_ID') and (has_secret or has_certificate)):
        _load_env()


CREDENTIAL_HELP = (
    "Missing required environment variables. Ensure the .env file contains TENANT_ID, CLIENT_ID "
    "and ONE of:\n"
    "  CERTIFICATE_PATH=<path to .pem or .pfx>   (optionally CERTIFICATE_PASSWORD)\n"
    "  CLIENT_SECRET=<your-client-secret>\n"
    "Run setup-service-principal.ps1 to create these credentials."
)


def _build_credential(tenant_id=None):
    """Build a service principal credential from the environment.

    Certificate auth is used when CERTIFICATE_PATH is set, because it avoids keeping a
    long-lived secret in a plaintext file. Falls back to CLIENT_SECRET otherwise, so existing
    setups keep working unchanged.

    Returns:
        tuple: (credential, auth_method_name)
    """
    _ensure_env_loaded()

    tenant_id = tenant_id or os.getenv('TENANT_ID')
    client_id = os.getenv('CLIENT_ID')
    certificate_path = os.getenv('CERTIFICATE_PATH')
    client_secret = os.getenv('CLIENT_SECRET')

    if not (tenant_id and client_id):
        raise ValueError(CREDENTIAL_HELP)

    if certificate_path:
        certificate_path = os.path.expanduser(os.path.expandvars(certificate_path))
        if not os.path.exists(certificate_path):
            raise ValueError(
                f"CERTIFICATE_PATH points to a file that does not exist: {certificate_path}"
            )
        password = os.getenv('CERTIFICATE_PASSWORD') or None
        return CertificateCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            certificate_path=certificate_path,
            password=password.encode() if isinstance(password, str) else password,
        ), 'certificate'

    if client_secret:
        return ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        ), 'client secret'

    raise ValueError(CREDENTIAL_HELP)

# Module-level cache for clients
_graph_client = None
_credential = None

async def get_graph_client(tenant_id=None, silent=False):
    """Get Microsoft Graph SDK client using service principal authentication
    
    Args:
        tenant_id: Azure tenant ID (optional)
        silent: If True, suppress authentication messages (for background license checks)
    
    Args:
        tenant_id: Azure tenant ID (optional, read from .env if not provided)
        
    Returns:
        GraphServiceClient instance
    """
    global _graph_client, _credential
    
    if _graph_client:
        return _graph_client

    _ensure_env_loaded()
    
    from .spinner import get_timestamp

    # Create credential using service principal (certificate or client secret)
    if _credential is None:
        _credential, auth_method = _build_credential(tenant_id)
    else:
        auth_method = 'certificate' if os.getenv('CERTIFICATE_PATH') else 'client secret'

    if not silent:
        print(f"[{get_timestamp()}] ℹ️     Authenticating with service principal ({auth_method})...")
        import sys
        sys.stdout.flush()
    
    # Create Graph client
    _graph_client = GraphServiceClient(
        credentials=_credential,
        scopes=['https://graph.microsoft.com/.default']
    )
    
    if not silent:
        print(f"[{get_timestamp()}] ✅ Authenticated successfully")
        import sys
        sys.stdout.flush()
    return _graph_client

def get_shared_credential():
    """Get shared credential for non-Graph APIs (Defender, Power Platform)
    
    Returns:
        ClientSecretCredential instance
    """
    global _credential
    
    if _credential is not None:
        return _credential

    _credential, _ = _build_credential()

    return _credential

def get_power_platform_credential():
    """Get credential for Power Platform APIs
    
    Returns the same shared credential (service principal).
    
    Returns:
        ClientSecretCredential instance
    """
    return get_shared_credential()

async def get_api_client(service_name):
    """Get HTTP client with bearer token for specific API
    
    Args:
        service_name: One of 'defender', 'power_platform'
    
    Returns:
        httpx.AsyncClient with authorization header
    """
    credential = get_shared_credential()
    
    # Define scopes and base URLs for each service
    service_config = {
        'defender': {
            'scope': 'https://api.security.microsoft.com/.default',
            'base_url': 'https://api.security.microsoft.com'
        },
        'power_platform': {
            'scope': 'https://service.powerapps.com/.default',
            'base_url': 'https://service.powerapps.com'
        }
    }
    
    if service_name not in service_config:
        raise ValueError(f"Unknown service: {service_name}. Valid: {list(service_config.keys())}")
    
    config = service_config[service_name]
    
    # Get token for the specific scope (synchronous call)
    token = credential.get_token(config['scope'])
    
    # Create HTTP client with bearer token
    return httpx.AsyncClient(
        base_url=config['base_url'],
        headers={
            "Authorization": f"Bearer {token.token}",
            "Accept": "application/json",
            "Content-Type": "application/json"
        },
        timeout=30.0
    )
