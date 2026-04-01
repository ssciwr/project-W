from typing import Annotated, Sequence

from fastapi import APIRouter, Depends, HTTPException, Response, status
from project_W_lib.models.response_models import (
    ErrorResponse,
    TokenInfoResponse,
    UserResponse,
    UserTypeEnum,
)

import project_W.dependencies as dp

from ..models.internal_models import LoginContext
from ..security.auth import (
    auth_dependency_responses,
    unset_cookie,
    validate_user,
)

router = APIRouter(
    prefix="/users",
    tags=["users"],
    responses=auth_dependency_responses,
)


@router.delete("/invalidate_token")
async def invalidate_token(
    login_context: Annotated[
        LoginContext,
        Depends(validate_user(require_verified=False, require_admin=False, require_tos=False)),
    ],
    token_id: int,
) -> str:
    """
    Invalidate the local token with the provided id. After calling this route the token with the provided id can't be used anymore.
    """
    await dp.db.delete_token_of_user(login_context.user.id, token_id)
    return "Success"


@router.delete("/invalidate_all_tokens")
async def invalidate_all_tokens(
    login_context: Annotated[
        LoginContext,
        Depends(validate_user(require_verified=False, require_admin=False, require_tos=False)),
    ],
) -> str:
    """
    Invalidate all local tokens of the logged in user account. After calling this route all local tokens of the logged in user account won't work anymore.
    """
    await dp.db.delete_all_tokens_of_user(int(login_context.user.id))
    return "Success"


@router.delete("/logout")
async def logout(
    login_context: Annotated[
        LoginContext,
        Depends(validate_user(require_verified=False, require_admin=False, require_tos=False)),
    ],
    response: Response,
) -> str:
    """
    Like /invalidate_token, but invalidates the token currently being used and additionally unsets the token cookie
    """
    unset_cookie(response)
    await dp.db.delete_token_of_user(login_context.user.id, login_context.token.id)
    return "Success"


@router.post(
    "/get_new_api_token",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Creation of api tokens is disabled for your identity provider",
        }
    },
)
async def get_new_api_token(
    login_context: Annotated[
        LoginContext,
        Depends(validate_user(require_verified=True, require_admin=False, require_tos=False)),
    ],
    name: str,
) -> str:
    """
    Create a new API token. The main difference between API tokens and regular auth tokens is that API tokens never expire. Only create them if necessary and only use a different token for each device/service so that it is easy to invalidate one of them if a device gets compromised. The provided name has the purpose of being able to identify which token belongs to which device/service.THe successfully response contains the newly created token.
    """
    # check if current user is from a provider which allows creation of api tokens
    disabled_exc = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Creation of api tokens is disabled for your account type. Login using a different provider or ask the administrator to enable this.",
    )
    if (
        login_context.user.user_type == UserTypeEnum.OIDC
        and not dp.config.security.oidc_providers[
            login_context.user.provider_name
        ].allow_creation_of_api_tokens
    ):
        raise disabled_exc
    if (
        login_context.user.user_type == UserTypeEnum.LDAP
        and not dp.config.security.ldap_providers[
            login_context.user.provider_name
        ].allow_creation_of_api_tokens
    ):
        raise disabled_exc
    if (
        login_context.user.user_type == UserTypeEnum.LOCAL
        and not dp.config.security.local_account.allow_creation_of_api_tokens
    ):
        raise disabled_exc

    token = await dp.db.add_new_user_token(
        login_context.user.id, name, True, False, None, login_context.token.oidc_refresh_token_id
    )
    return token.root.get_secret_value()


@router.get("/get_all_token_info")
async def get_all_token_info(
    login_context: Annotated[
        LoginContext,
        Depends(validate_user(require_verified=False, require_admin=False, require_tos=False)),
    ],
) -> Sequence[TokenInfoResponse]:
    """
    Get a list of all token id's and names currently in use by this account, sorted by last usage timestamp.
    """
    return await dp.db.get_info_of_all_tokens_of_user(login_context.user.id)


@router.get("/info")
async def info(
    login_context: Annotated[
        LoginContext,
        Depends(validate_user(require_verified=False, require_admin=False, require_tos=False)),
    ],
) -> UserResponse:
    """
    This route returns all information about the currently logged in user.
    """
    return login_context.user


@router.post(
    "/accept_tos",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "No term of service with that id or version exists",
        }
    },
)
async def accept_tos(
    login_context: Annotated[
        LoginContext,
        Depends(validate_user(require_verified=False, require_admin=False, require_tos=False)),
    ],
    tos_id: int,
    tos_version: int,
) -> str:
    """
    By calling this route the user accepts to the terms of service specified by the submitted tos_id and tos_version. Only if a user has accepted the newest version of every term of service of this instance they are allowed to use this service.
    """
    for server_tos_id, server_tos in dp.config.terms_of_services.items():
        if tos_id == server_tos_id and tos_version <= server_tos.version:
            await dp.db.accept_tos_of_user(login_context.user.id, tos_id, tos_version)
            return "Success"
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"No term of service found with id {tos_id} and version {tos_version}",
    )


@router.delete("/delete")
async def delete(
    login_context: Annotated[
        LoginContext,
        Depends(
            validate_user(
                require_verified=False,
                require_admin=False,
                require_tos=False,
                no_provisioned_users=True,
            )
        ),
    ],
) -> str:
    """
    Deletes the currently logged in user and all information related to it (like jobs, tokens, etc.)
    """
    await dp.db.delete_user(login_context.user.id)
    return "Success"
