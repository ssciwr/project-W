from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Response, status
from fastapi.security import APIKeyCookie, HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError
from project_W_lib.models.base import Token
from project_W_lib.models.response_models import ErrorResponse, UserResponse, UserTypeEnum

import project_W.dependencies as dp

from ..models.internal_models import (
    LdapUserInternal,
    LocalUserInternal,
    LoginContext,
    OidcUserInternal,
    OnlineRunner,
)
from .oidc_deps import get_provider_name

# define all possible HTTP responses here so that they can be included together with the dependency for the docs
auth_dependency_responses: dict[int | str, dict[str, Any]] = {
    401: {
        "model": ErrorResponse,
        "headers": {
            "WWW-Authenticate": {
                "type": "string",
            }
        },
        "description": "Validation error of auth token, or not authenticated",
    },
    403: {
        "model": ErrorResponse,
        "headers": {
            "WWW-Authenticate": {
                "type": "string",
            }
        },
        "description": "Token doesn't grand enough permissions",
    },
}
runner_dependency_responses: dict[int | str, dict[str, Any]] = {
    401: {
        "model": ErrorResponse,
        "headers": {
            "WWW-Authenticate": {
                "type": "string",
            }
        },
        "description": "No runner with that token exists, nor not authenticated",
    },
}
online_runner_dependency_responses: dict[int | str, dict[str, Any]] = {
    403: {
        "model": ErrorResponse,
        "description": "This runner is currently not registered as online, or session_token is invalid",
    },
}

get_bearer_http_credential = HTTPBearer(
    bearerFormat="Bearer",
    scheme_name="Auth & runner token",
    description="""
    A valid auth token returned by one of the following login routes:
    - local account: /local-account/login
    - oidc: /oidc/login/{idp_name}
    - ldap: /ldap/login/{idp_name}
    Which of these are available with what idp's depends on the server configuration
    This is the same token as in the APIKeyCookie 'Auth token' authentication, but in an HTTPBearer format which might be nicer for scripting and so on using the long lived API tokens.
    The runners will also put a runner token here, obtained from the /admins/create_runner route
    """,
    auto_error=False,
)


def get_bearer(
    token: Annotated[HTTPAuthorizationCredentials | None, Depends(get_bearer_http_credential)],
) -> Token | None:
    if token is None:
        return None
    else:
        try:
            return Token.model_validate(token.credentials)
        except ValidationError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
            )


get_cookie_str = APIKeyCookie(
    name="token",
    scheme_name="Auth token",
    description="""
    A valid auth token returned by one of the following login routes:
    - local account: /local-account/login
    - oidc: /oidc/login/{idp_name}
    - ldap: /ldap/login/{idp_name}
    Which of these are available with what idp's depends on the server configuration
    This is the same token as in the HTTPBearer 'Auth token' authentication, but in a cookie format which is nicer for inside the browser with temporary sessions.
    For the /runners/ routes you need to obtain a runner token from the /admins/create_runner route
    """,
    auto_error=False,
)


def get_cookie(token: Annotated[str | None, Depends(get_cookie_str)]) -> Token | None:
    if token is None:
        return None
    else:
        try:
            return Token.model_validate(token)
        except ValidationError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
            )


admin_user_scope = "admin"


def check_admin_privileges(scopes: list[str], is_admin: bool) -> bool:
    # make sure that we only assign scopes that the user has permissions for (currently only is_admin, can be replaced with attribute set in the future)
    admin_privileges = False
    for scope in scopes:
        if scope == admin_user_scope:
            if not is_admin:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Your user isn't an admin",
                )
            admin_privileges = True
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="We don't support that token permission scope",
            )
    return admin_privileges


def set_token_cookie(response: Response, token: Token):
    max_age_secs = dp.config.security.tokens.session_expiration_time_minutes * 60
    response.set_cookie(key="token", value=token.root.get_secret_value(), max_age=max_age_secs)


def unset_cookie(response: Response):
    response.set_cookie(key="token", value="")


def validate_user(
    require_verified: bool,
    require_admin: bool,
    require_tos: bool,
    no_provisioned_users: bool = False,
):
    async def user_validation_dep(
        bearer_token: Annotated[Token | None, Depends(get_bearer)],
        cookie_token: Annotated[Token | None, Depends(get_cookie)],
        response: Response,
    ) -> LoginContext:
        if bearer_token is not None:
            token = bearer_token
        elif cookie_token is not None:
            token = cookie_token
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
            )

        result = await dp.db.get_user_by_token(token)
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate token",
            )
        (user, token_info) = result

        if isinstance(user, LocalUserInternal):
            user_type = UserTypeEnum.LOCAL
            provider_name = "project-W"
            is_verified = user.is_verified
        elif isinstance(user, OidcUserInternal):
            user_type = UserTypeEnum.OIDC
            provider_name = get_provider_name(user.iss)
            is_verified = True
        elif isinstance(user, LdapUserInternal):
            user_type = UserTypeEnum.LDAP
            provider_name = user.provider_name
            is_verified = True
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Couldn't read user type from database!",
            )

        if require_verified and not is_verified:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Your email address needs to be verified to access this route. Please click on the link sent to '{user.email.root}' or request a new confirmation email.",
            )

        if require_admin and not token_info.admin_privileges:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not enough permissions",
            )

        if require_tos:
            for tos_id, tos in dp.config.terms_of_services.items():
                accepted_tos_version = user.accepted_tos.get(tos_id)
                if accepted_tos_version is None or accepted_tos_version < tos.version:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="You need to agree to the newest version of the terms of services of this instance to access this route",
                    )

        if (
            no_provisioned_users
            and isinstance(user, LocalUserInternal)
            and user.provision_number is not None
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This route cannot be called by the current user because they have been provisioned through the admin config file. Please change any user attributes there instead.",
            )

        # rolling tokens (if not explicitly created): grant new token if this one is about to expire
        if not token_info.explicit and token_info.expires_at is not None:
            minutes_until_expiration = int(
                (token_info.expires_at - datetime.now(timezone.utc)).total_seconds() // 60
            )
            if (
                minutes_until_expiration
                < dp.config.security.tokens.rolling_session_before_expiration_minutes
            ):
                new_token = await dp.db.rotate_user_token(
                    token_info.id, dp.config.security.tokens.session_expiration_time_minutes
                )
                set_token_cookie(response, new_token)

        return LoginContext(
            user=UserResponse(
                id=user.id,
                user_type=user_type,
                email=user.email,
                provider_name=provider_name,
                is_verified=is_verified,
                accepted_tos=user.accepted_tos,
            ),
            token=token_info,
        )

    return user_validation_dep


async def validate_runner(
    token: Annotated[Token | None, Depends(get_bearer)],
) -> int:
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    if (runner_id := await dp.db.get_runner_by_token(token)) is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No runner with that token exists!",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return runner_id


async def validate_online_runner(
    runner_id: Annotated[int, Depends(validate_runner)], session_token: str
) -> OnlineRunner:
    try:
        session_token_validated = Token.model_validate(session_token)
    except ValidationError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    if not (online_runner := await dp.ch.get_online_runner_by_id(runner_id)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This runner is currently not registered as online!",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if online_runner.session_token_hash != session_token_validated.hash():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The session token is invalid. Your runner token might have been compromised and used to re-register this runner on a different machine, if this wasn't you then immediately invalidate this runner! Refer to the documentation for how to do so",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return online_runner
