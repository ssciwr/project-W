from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from logging import getLogger
from project_W_lib.models.response_models import LocalAccountOperationModeEnum

import project_W.dependencies as dp
from project_W_lib.models.shared_setting_models import LoggingEnum

from ._version import __version__
from .caching import RedisAdapter
from .database import PostgresAdapter
from .routers import general, admins, jobs, ldap, local_account, oidc, runners, users
from .security import ldap_deps, oidc_deps
from .smtp import SmtpClient


# startup database connections before spinning up application
@asynccontextmanager
async def lifespan(app: FastAPI):
    # connect to database
    dp.db = PostgresAdapter(str(dp.config.postgres_connection_string))
    await dp.db.open()

    # connect to caching server
    dp.ch = RedisAdapter()
    await dp.ch.open(dp.config.redis_connection)

    # connect to smtp server
    dp.smtp = SmtpClient(dp.config.smtp_server)
    await dp.smtp.open()

    # include security routers depending on which authentication backends are configured in config file
    login_method_exists = False
    if dp.config.security.oidc_providers:
        login_method_exists = True
        app.include_router(oidc.router, prefix="/api")
        await oidc_deps.register_with_oidc_providers(dp.config)
    if dp.config.security.ldap_providers:
        login_method_exists = True
        app.include_router(ldap.router, prefix="/api")
        ldap_deps.ldap_adapter = ldap_deps.LdapAdapter()
        await ldap_deps.ldap_adapter.open(dp.config.security.ldap_providers)
    if dp.config.security.local_account.mode != LocalAccountOperationModeEnum.DISABLED:
        login_method_exists = True
        app.include_router(local_account.router, prefix="/api")
        for prov_num, prov_user in dp.config.security.local_account.user_provisioning.items():
            await dp.db.ensure_local_user_is_provisioned(
                prov_num, prov_user.email, prov_user.password, prov_user.is_admin
            )
    if not login_method_exists:
        raise Exception(
            "No login method (one of local_account, ldap or oidc) has been specified in the config!"
        )

    # include app mount (it is important that this happens after all routers have been included!)
    if dp.client_path is not None:
        app.mount(
            "/",
            StaticFiles(directory=dp.client_path.resolve(), html=True),
            name="client app",
        )

    # enqueue all jobs from the database that are not finished yet
    for job_id, user_id, aborting in await dp.db.get_all_unfinished_jobs():
        if aborting:
            await dp.db.finish_failed_job(job_id, "Job was aborted")
        await dp.ch.enqueue_new_job(job_id, job_id * -1)
        await dp.ch.assign_job_to_runner_if_possible(job_id, user_id)

    yield

    # close connections before application exit
    await dp.db.close()
    await dp.ch.close()
    await dp.smtp.close()
    if dp.config.security.ldap_providers:
        await ldap_deps.ldap_adapter.close()


# this can be in Markdown
app_description = """
## About Project-W

Project-W is a platform for creating transcripts of audio files (speech-to-text). It leverages OpenAIs Whisper models for the transcription while providing an API and easy-to-use interface for users to create and manage transcription jobs.

## More documentation

In addition to [the OpenAPI doc](/docs) there is also [a Redoc doc](/redoc) available if you prefer.

If you are looking for documentation about something else than the API then refer to the [full Project-W documentation](https://project-w.readthedocs.io). You can also find the [source code of all Project-W components on GitHub](https://github.com/ssciwr/project-w).
"""
app_tags_metadata = [
    {
        "name": "general",
        "description": "Routes that return information regarding the whole application. Not authenticated.",
    },
    {
        "name": "users",
        "description": "For regular users to manage their accounts",
    },
    {
        "name": "local-account",
        "description": "Login, Signup and account management routes for local Project-W accounts",
    },
    {
        "name": "oidc",
        "description": "Login/Signup routes for OIDC authentication providers. If no routes are shown here then this instance has no OIDC provider configured.",
    },
    {
        "name": "ldap",
        "description": "Login/Signup routes for LDAP authentication providers. If no routes are shown here then this instance has no LDAP provider configured.",
    },
    {
        "name": "jobs",
        "description": "Job submission and management routes",
    },
    {
        "name": "admins",
        "description": "For admin users to manage the server and all users on it",
    },
    {
        "name": "runners",
        "description": "Routes used by Project-W runners to communicate with the backend, retrieve jobs and submit transcripts",
    },
]


def custom_generate_unique_id(route: APIRoute):
    """
    for nicer function names in clients/frontends
    """
    return f"{route.tags[0]}-{route.name}"


app = FastAPI(
    title="Project-W",
    description=app_description,
    summary="Create transcripts of audio files (speech-to-text)!",
    version=__version__,
    license_info={
        "name": "GNU Affero General Public License Version 3",
        "identifier": "AGPL-3.0-only",
        "url": "https://www.gnu.org/licenses/agpl-3.0.txt",
    },
    openapi_tags=app_tags_metadata,
    lifespan=lifespan,
    root_path=(
        dp.config.web_server.reverse_proxy.root_path
        if dp.config.web_server.reverse_proxy and dp.config.web_server.reverse_proxy.root_path
        else ""
    ),
    root_path_in_servers=False,
    # add imprint info to app attributes (so that it is displayed in OpenAPI docs as well)
    contact=(
        {
            "name": dp.config.imprint.name,
            "email": dp.config.imprint.email.root if dp.config.imprint.email is not None else None,
            "url": (
                dp.config.imprint.url
                if dp.config.imprint.url is not None
                else f"{dp.config.client_url}/imprint"
            ),
        }
        if dp.config.imprint
        else None
    ),
    terms_of_service=f"{dp.config.client_url}/tos" if dp.config.terms_of_services else None,
    generate_unique_id_function=custom_generate_unique_id,
)
# logging middleware for debug mode
if (
    dp.config.logging.console.level == LoggingEnum.DEBUG
    or dp.config.logging.file.level == LoggingEnum.DEBUG
):
    middleware_logger = getLogger("project-W.middleware")

    @app.middleware("http")
    async def logging_middleware(request: Request, call_next):
        middleware_logger.debug(
            "Incoming request",
            extra={
                "url_path": request.url.path,
                "url_host": request.url.hostname,
                "scheme": request.url.scheme,
                "method": request.method,
                "headers": dict(request.headers),
                "query_params": request.query_params,
                "path_params": request.path_params,
                "cookies": request.cookies,
                "client": request.client,
            },
        )
        response = await call_next(request)
        middleware_logger.debug(
            "Outgoing response",
            extra={
                "status": response.status_code,
                "headers": dict(response.headers),
            },
        )
        return response


# middleware required by authlib for oidc
app.add_middleware(
    SessionMiddleware,
    secret_key=dp.config.security.secret_key.root.get_secret_value(),
)
# middleware to guard against HTTP Host header attacks
app.add_middleware(TrustedHostMiddleware, allowed_hosts=dp.config.web_server.allowed_hosts)
# middleware to handle proxy headers and rewrite Host header accordingly
if dp.config.web_server.reverse_proxy is not None:
    app.add_middleware(
        ProxyHeadersMiddleware, trusted_hosts=dp.config.web_server.reverse_proxy.trusted_proxies
    )

app.include_router(general.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(admins.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(runners.router, prefix="/api")
