import json
import re
import secrets
import shutil
import ssl
import subprocess
import httpx
import psycopg
import pytest
import redis

from contextlib import contextmanager

from .conftest_utils import get_cert_dir_from_backend_dir, generate_self_signed_certs, AuthMethod, login_client
from .helper_functions import wait_for_backend


@pytest.fixture(scope="function")
def backend(request, smtpd, tmp_path):
    BACKEND_PORT = 5001
    BACKEND_BASE_URL = f"https://localhost:{BACKEND_PORT}"

    generate_self_signed_certs(tmp_path)

    print(f"===== Server tmp path: {tmp_path} =====")
    log_file_path = (tmp_path / "backend.log").resolve()
    postgres_conn = "postgresql://project_w@localhost:5432"
    redis_conn = "redis://localhost:6379/project-W"
    settings = {
        "client_url": f"{BACKEND_BASE_URL}/#",
        "web_server": {
            "ssl": {
                "cert_file": "/etc/xdg/project-W/certs/cert.pem",
                "key_file": "/etc/xdg/project-W/certs/key.pem",
            },
            "port": BACKEND_PORT,
        },
        "smtp_server": {
            "hostname": smtpd.hostname,
            "port": smtpd.port,
            "secure": "plain",
            "sender_email": "ci@example.org",
        },
        "postgres_connection_string": postgres_conn,
        "redis_connection": {
            "connection_string": redis_conn,
        },
        "security": {
            "secret_key": secrets.token_hex(32),
            "local_account": {
                "user_provisioning": {
                    0: {
                        "email": "admin_user@example.org",
                        "password": "Password1234!",
                        "is_admin": True,
                    },
                    1: {
                        "email": "normal_user@example.org",
                        "password": "Password1234!",
                        "is_admin": False,
                    },
                },
            },
            "oidc_providers": {
                "KeyCloak": {
                    "icon_url": "https://www.keycloak.org/resources/favicon.svg",
                    "base_url": "http://127.0.0.1:8081/realms/project-w_realm",
                    "client_id": "project-w",
                    "client_secret": "787XYwQNoRe1HiKBRaGYsZCOo0D31oyv",
                    "user_role": {
                        "field_name": "group",
                        "name": "users",
                    },
                    "admin_role": {
                        "field_name": "group",
                        "name": "admins",
                    },
                },
            },
            "ldap_providers": {
                "OpenLDAP": {
                    "server_address": "ldap://127.0.0.1:3000",
                    "service_account_auth": {
                        "user": "cn=admin,dc=example,dc=org",
                        "password": "admin",
                    },
                    "username_attributes": [ "cn", "mail" ],
                    "uid_attribute": "uid",
                    "mail_attribute": "mail",
                    "user_query": {
                        "base_dn": "ou=users,dc=example,dc=org",
                        "filter": "objectClass=inetOrgPerson",
                    },
                    "admin_query": {
                        "base_dn": "ou=admins,dc=example,dc=org",
                        "filter": "objectClass=inetOrgPerson",
                    },
                },
            },
        },
        "imprint": request.param[0],
        "logging": {
            "file": {
                "path": "/etc/xdg/project-W/backend.log",
                "level": "DEBUG",
                "json_fmt": True,
            }
        }
    }

    with open((tmp_path / "config.yml").resolve(), "w") as f:
        json.dump(settings, f)

    subprocess.run(
        [
            "docker",
            "run",
            "--name",
            "Project-W",
            "--rm",
            "--stop-timeout",
            "5",
            "--network",
            "host",
            "-v",
            f"{tmp_path.resolve()}:/etc/xdg/project-W/",
            "-d",
            "project-w",
        ],
        check=True,
    )

    wait_for_backend(BACKEND_BASE_URL)
    yield (f"{BACKEND_BASE_URL}", smtpd, tmp_path)

    subprocess.run(
        [
            "docker",
            "stop",
            "Project-W",
        ],
        check=True,
    )

    #print logs
    with open(log_file_path, "r") as file:
        print(f"===== Printing logs of backend from {log_file_path} =====")
        print(file.read())

    #clean database
    with psycopg.connect(postgres_conn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DROP SCHEMA project_w CASCADE
            """
            )

    #clean cache
    r = redis.Redis()
    redis_client = r.from_url(redis_conn)
    redis_client.flushdb()


@pytest.fixture(scope="function")
def get_client(backend):
    clients = []
    cert_dir = get_cert_dir_from_backend_dir(backend[2])

    def _client_factory():
        cafile = (cert_dir / "cert.pem").resolve()
        ctx = ssl.create_default_context(cafile=cafile)
        client = httpx.Client(base_url=backend[0], verify=ctx)
        clients.append(client)
        return client

    yield _client_factory

    for client in clients:
        client.close()


@pytest.fixture(scope="function", params=[e.value for e in AuthMethod])
def get_logged_in_client(request, get_client):
    """
    Gets a logged in session (as a normal user)
    Runs 4 times using different login methods (local account, ldap, API token of local account, API token of ldap account)
    Currently does not test OIDC since browser login flow is difficult to simulate in CI
    """
    def _client_factory():
        client = get_client()
        return login_client(client, AuthMethod(request.param), False)

    return _client_factory


@pytest.fixture(scope="function", params=["local", "ldap"])
def get_logged_in_admin_client(request, get_client):
    """
    Gets a logged in session (as an admin user)
    Runs 2 times using different login methods (local account, ldap)
    Currently does not test OIDC since browser login flow is difficult to simulate in CI
    API tokens not used here since they cannot have admin privileges
    """
    def _client_factory():
        client = get_client()
        return login_client(client, AuthMethod(request.param), True)

    return _client_factory


@pytest.fixture(scope="function")
def runner(backend, get_client, tmp_path):
    @contextmanager
    def _runner_factory(name: str, priority: int):
        client = login_client(get_client(), AuthMethod.LOCAL, True)
        response = client.post("/api/admins/create_runner")
        response.raise_for_status()
        content = response.json()
        runner_id = content["id"]

        print(f"===== Runner {runner_id} tmp path: {tmp_path} =====")
        log_file_path = (tmp_path / "runner.log").resolve()
        settings = {
            "runner_attributes": {
                "name": name,
                "priority": priority,
            },
            "backend_settings": {
                "url": backend[0],
                "auth_token": content["token"],
                "ca_pem_file_path": "/etc/xdg/project-W-runner/backend-cert.pem",
            },
            "whisper_settings": {
                "hf_token": "abcd",
            },
            "logging": {
                "file": {
                    "path": "/etc/xdg/project-W-runner/runner.log",
                    "level": "DEBUG",
                    "json_fmt": True,
                }
            }
        }

        cert_dir = get_cert_dir_from_backend_dir(backend[2])
        shutil.copyfile((cert_dir / "cert.pem").resolve(), ((tmp_path / "backend-cert.pem").resolve()))
        with open((tmp_path / "config.yml").resolve(), "w") as f:
            json.dump(settings, f)

        normalized_name = re.sub("[^a-zA-Z0-9_.-]", "_", name)
        container_name = f"runner-{normalized_name}"
        subprocess.run(
            [
                "docker",
                "run",
                "--name",
                container_name,
                "--rm",
                "--stop-timeout",
                "5",
                "--network",
                "host",
                "-v",
                f"{tmp_path.resolve()}:/etc/xdg/project-W-runner/",
                "-d",
                "project-w_runner_dummy",
            ],
            check=True,
        )

        yield runner_id

        subprocess.run(
            [
                "docker",
                "stop",
                container_name,
            ],
            check=True,
        )

        #print logs
        with open(log_file_path, "r") as file:
            print(f"===== Printing logs of runner {runner_id} from {log_file_path} =====")
            print(file.read())

    return _runner_factory
