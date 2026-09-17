from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from Core.certificate_validation import inspect_graph_certificate


def certificate_fixture(path, password="test-only-password", *, expired=False, future=False, pem=False, mismatch=False, public_only=False, elliptic=False):
    key = ec.generate_private_key(ec.SECP256R1()) if elliptic else rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Offline setup test")])
    certificate = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now + timedelta(days=1) if future else now - timedelta(days=2))
        .not_valid_after(now - timedelta(days=1) if expired else now + timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    encryption = serialization.BestAvailableEncryption(password.encode()) if password else serialization.NoEncryption()
    if public_only:
        data = certificate.public_bytes(serialization.Encoding.PEM)
    elif pem:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048) if mismatch else key
        data = certificate.public_bytes(serialization.Encoding.PEM) + private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, encryption)
    else:
        data = pkcs12.serialize_key_and_certificates(b"test", key, certificate, None, encryption)
    Path(path).write_bytes(data)
    return certificate.fingerprint(hashes.SHA1()).hex().upper()


class GraphCertificateValidationTests(unittest.TestCase):
    def test_encrypted_pfx_and_pem_are_usable_and_return_only_public_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            for pem in (False, True):
                path = Path(folder) / ("certificate.pem" if pem else "certificate.pfx")
                thumbprint = certificate_fixture(path, password="test-ü-password", pem=pem)
                result = inspect_graph_certificate(path, "test-ü-password")
                self.assertEqual(thumbprint, result["thumbprint"])
                self.assertEqual({"thumbprint", "not_before", "not_after"}, set(result))

    def test_invalid_credentials_fail_without_echoing_password(self):
        cases = (
            ({}, "wrong-password"),
            ({"expired": True}, "test-only-password"),
            ({"future": True}, "test-only-password"),
            ({"pem": True, "mismatch": True}, "test-only-password"),
            ({"public_only": True}, "test-only-password"),
            ({"elliptic": True}, "test-only-password"),
        )
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "certificate"
            for options, supplied_password in cases:
                with self.subTest(options=options):
                    certificate_fixture(path, **options)
                    with self.assertRaises(ValueError) as raised:
                        inspect_graph_certificate(path, supplied_password)
                    self.assertNotIn(supplied_password, str(raised.exception))
            path.write_bytes(b"invalid certificate")
            with self.assertRaises(ValueError):
                inspect_graph_certificate(path)

    def test_stdin_validator_does_not_emit_password_or_private_key(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "certificate.pfx"
            certificate_fixture(path)
            for password in ("test-only-password", "wrong-password"):
                result = subprocess.run(
                    [sys.executable, str(Path(__file__).resolve().parents[1] / "Core/certificate_validation.py")],
                    input=json.dumps({"path": str(path), "password": password}),
                    capture_output=True, text=True, timeout=15,
                )
                self.assertEqual(password == "test-only-password", json.loads(result.stdout)["valid"])
                self.assertNotIn(password, result.stdout + result.stderr)
                self.assertNotIn("PRIVATE KEY", result.stdout + result.stderr)

    def test_utf8_stdin_accepts_optional_bom_with_non_ascii_path_and_password(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "certificate-\u00fc.pfx"
            password = "test-\u00fc-\u03a9-password"
            thumbprint = certificate_fixture(path, password=password)
            request = json.dumps({"path": str(path), "password": password}, ensure_ascii=False)
            for encoding in ("utf-8", "utf-8-sig"):
                with self.subTest(encoding=encoding):
                    result = subprocess.run(
                        [sys.executable, str(Path(__file__).resolve().parents[1] / "Core/certificate_validation.py")],
                        input=request.encode(encoding), capture_output=True, timeout=15,
                        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
                    )
                    self.assertEqual(0, result.returncode, result.stderr.decode("utf-8"))
                    metadata = json.loads(result.stdout)
                    self.assertTrue(metadata["valid"])
                    self.assertEqual(thumbprint, metadata["thumbprint"])
                    self.assertNotIn(password.encode("utf-8"), result.stdout + result.stderr)
                    self.assertNotIn(b"PRIVATE KEY", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
