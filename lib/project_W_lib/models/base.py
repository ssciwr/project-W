import re
import secrets
import base64
import hashlib

from pydantic import BaseModel, RootModel, model_validator, SecretStr
from typing import Any, Self
from email_validator import EmailNotValidError, validate_email

class EmailValidated(RootModel):
    root: str

    @model_validator(mode="before")
    @classmethod
    def email_validation(cls, data: Any) -> Any:
        if isinstance(data, str):
            try:
                val_email = validate_email(data, check_deliverability=False)
                cls.__original = val_email.original
                cls.__domain = val_email.domain
                cls.__local_part = val_email.local_part
                return val_email.normalized
            except EmailNotValidError as e:
                raise ValueError(e)

    def get_domain(self) -> str:
        return self.__domain

    def get_original(self) -> str:
        return self.__original

    def get_local_part(self) -> str:
        return self.__local_part


class PasswordValidated(RootModel):
    root: SecretStr

    @model_validator(mode="after")
    def password_validation(self) -> Self:
        match = re.match(
            r"^(?=.*[A-Z])(?=.*[a-z])(?=.*[0-9])(?=.*[^a-zA-Z0-9]).{12,}$",
            self.root.get_secret_value(),
        )
        if match is None:
            raise ValueError(
                "The password needs to have at least one lowercase letter, uppercase letter, number, special character and at least 12 characters in total"
            )
        return self


class Token(RootModel):
    root: SecretStr

    def __init__(self) -> None:
        super().__init__(secrets.token_urlsafe())

    def hash(self) -> str:
        """
        We only store the hash of the token, otherwise a db leak would make
        it possible to impersonate any runner/user. We don't need to use a salted hash
        because the token is created by the server and already has sufficient entropy.
        The hash itself is stored using base64.
        """
        return (
            base64.urlsafe_b64encode(hashlib.sha256(self.root.get_secret_value().encode("ascii")).digest())
            .rstrip(b"=")
            .decode("ascii")
        )

    @model_validator(mode="after")
    def password_validation(self) -> Self:
        match = re.match(
            r"^[A-Za-z0-9_-]+$",
            self.root.get_secret_value(),
        )
        if match is None:
            raise ValueError(
                "Invalid token formatting, the token must be urlsafe base64 decodable"
            )
        return self

#don't set default values in this model since it's imported in repsonse_models
class UserBase(BaseModel):
    id: int
    email: EmailValidated
    accepted_tos: dict[int, int]
