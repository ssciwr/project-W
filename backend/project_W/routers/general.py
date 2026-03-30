from fastapi import APIRouter
from project_W_lib.models.response_models import AboutResponse, AuthSettingsResponse

import project_W.dependencies as dp

from .._version import __version__

router = APIRouter(
    tags=["general"],
)


@router.get("/about")
async def about() -> AboutResponse:
    """
    Returns a brief description of Project-W, a link to the GitHub repository containing the backend's code, the backend's version currently running on the system as well as the imprint of this instance (if it was configured by the instance's admin).
    """
    return AboutResponse(
        description="A self-hostable platform on which users can create transcripts of their audio files (speech-to-text) using Whisper AI",
        source_code="https://github.com/ssciwr/project-W/tree/main/backend",
        version=__version__,
        git_hash=dp.git_hash,
        imprint=dp.config.imprint,
        terms_of_services=dp.config.terms_of_services,
        job_retention_in_days=dp.config.cleanup.finished_job_retention_in_days,
        site_banners=await dp.db.list_site_banners(),
    )


@router.get("/auth_settings")
async def auth_settings() -> AuthSettingsResponse:
    """
    Returns all information required by the client regarding which account types and identity providers this instance supports, whether account signup of local accounts is allowed, whether the creation of API tokens is allowed for each account type and so on.
    """
    return AuthSettingsResponse(
        local_account=dp.config.security.local_account,
        oidc_providers=dp.config.security.oidc_providers,
        ldap_providers=dp.config.security.ldap_providers,
    )
