from enum import Enum
from httpx import Client

from datetime import datetime, timedelta, timezone
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pathlib import Path


def get_cert_dir_from_backend_dir(backend_dir: Path) -> Path:
    dir = (backend_dir / "certs").resolve()
    dir.mkdir(parents=True, exist_ok=True)
    return dir


def generate_self_signed_certs(backend_dir: Path):
    dir = get_cert_dir_from_backend_dir(backend_dir)
    key_path = dir / "key.pem"
    cert_path = dir / "cert.pem"

    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=4096
    )

# Save private key to file (PEM format, no encryption)
    with open(key_path, "wb") as f:
        f.write(
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption()
            )
        )

    # 2. Build the self-signed certificate
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
    ])

    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))  # 1 year validity
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]),
            critical=False,
        )
        .sign(private_key, hashes.SHA256())
    )

    # Save certificate to file (PEM format)
    with open(cert_path, "wb") as f:
        f.write(certificate.public_bytes(serialization.Encoding.PEM))


class AuthMethod(Enum):
    LOCAL = "local"
    LDAP = "ldap"
    LOCAL_TOKEN = "local_token"
    LDAP_TOKEN = "ldap_token"

def login_client(client: Client, auth_method: AuthMethod, as_admin: bool) -> Client:
    data={ #we use the same auth data for local and ldap users
        "grant_type": "password",
        "username": "admin_user@example.org" if as_admin else "normal_user@example.org",
        "password": "Password1234!",
        "scope": "admin" if as_admin else "",
    }
    if auth_method == AuthMethod.LOCAL or auth_method == AuthMethod.LOCAL_TOKEN:
        response = client.post(
            "/api/local-account/login",
            data=data,
        )
    elif auth_method == AuthMethod.LDAP or auth_method == AuthMethod.LDAP_TOKEN:
        response = client.post(
            "/api/ldap/login/OpenLDAP",
            data=data,
        )
    response.raise_for_status()
    client.cookies = response.cookies

    if auth_method == AuthMethod.LOCAL_TOKEN or auth_method == AuthMethod.LDAP_TOKEN:
        if as_admin:
            raise Exception("API tokens cannot have admin privileges")
        response = client.post(
            "/api/users/get_new_api_token",
            params={"name": "test API token"},
        )
        response.raise_for_status()
        client.cookies = {}
        client.headers["Authorization"] = f"Bearer {response.json()}"

    return client
