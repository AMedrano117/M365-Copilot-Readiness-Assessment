"""Offline certificate checks shared with PowerShell setup. Never requests a token."""

import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import pkcs12


def inspect_graph_certificate(path, password=None):
    """Return public metadata only after checking the file can sign Graph assertions."""
    try:
        data = Path(os.path.expanduser(os.path.expandvars(path))).read_bytes()
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("The Graph certificate file cannot be read.") from exc
    password_bytes = password.encode("utf-8") if password else None
    try:
        if b"-----BEGIN" in data:
            certificate = x509.load_pem_x509_certificate(data)
            private_key = serialization.load_pem_private_key(data, password_bytes)
        else:
            private_key, certificate, _chain = pkcs12.load_key_and_certificates(data, password_bytes)
    except (ValueError, TypeError) as exc:
        raise ValueError("The Graph certificate file cannot be decrypted or parsed; check its format and password.") from exc
    if certificate is None or not isinstance(private_key, RSAPrivateKey):
        raise ValueError("The Graph certificate must contain an RSA private key.")
    public_format = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    if private_key.public_key().public_bytes(*public_format) != certificate.public_key().public_bytes(*public_format):
        raise ValueError("The Graph certificate and private key do not match.")
    not_before = certificate.not_valid_before_utc if hasattr(certificate, "not_valid_before_utc") else certificate.not_valid_before.replace(tzinfo=timezone.utc)
    not_after = certificate.not_valid_after_utc if hasattr(certificate, "not_valid_after_utc") else certificate.not_valid_after.replace(tzinfo=timezone.utc)
    if not (not_before <= datetime.now(timezone.utc) < not_after):
        raise ValueError("The Graph certificate is expired or not yet valid.")
    return {
        "thumbprint": certificate.fingerprint(hashes.SHA1()).hex().upper(),
        "not_before": not_before.isoformat(),
        "not_after": not_after.isoformat(),
    }


def main():
    # Passwords arrive on redirected stdin and are never copied to command arguments,
    # environment variables, files, output, or exception diagnostics.
    try:
        request = json.load(sys.stdin)
        metadata = inspect_graph_certificate(request["path"], request.get("password"))
    except ValueError as exc:
        print(json.dumps({"valid": False, "reason": str(exc)}))
        return 1
    except Exception:
        print(json.dumps({"valid": False, "reason": "The local Graph certificate validation failed."}))
        return 1
    print(json.dumps({"valid": True, **metadata}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
