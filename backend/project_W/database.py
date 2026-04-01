import json
from abc import ABC, abstractmethod
from base64 import b64decode, b64encode
from datetime import datetime, timedelta
from typing import AsyncGenerator, Callable, LiteralString

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from Crypto.Cipher import ChaCha20
from Crypto.Random import get_random_bytes
from fastapi import UploadFile
from psycopg import pq
from psycopg.connection_async import AsyncConnection
from psycopg.rows import class_row, dict_row, scalar_row
from psycopg.types.json import Jsonb
from psycopg_pool.pool_async import AsyncConnectionPool
from pydantic import SecretStr, ValidationError
from project_W_lib.models.base import EmailValidated, PasswordValidated, Token
from project_W_lib.models.request_models import JobSettingsRequest, Transcript, TranscriptTypeEnum
from project_W_lib.models.response_models import (
    RunnerCreatedResponse,
    SiteBannerResponse,
    JobSettingsResponse,
)

import project_W.dependencies as dp

from ._version import version, version_tuple
from .models.setting_models import SecretKeyValidated
from .models.internal_models import (
    JobSettingsResponseWrapped,
    JobAndSettingsInternal,
    JobInternal,
    JobSortKey,
    LdapTokenInfoInternal,
    LdapUserInternal,
    LocalUserInternal,
    OidcTokenInfoInternal,
    OidcUserInternal,
    OnlineRunner,
    RunnerInternal,
    TokenInfoInternal,
)
from .utils import minutes_from_now_to_datetime, parse_version_tuple


class DatabaseAdapter(ABC):
    """
    Important semantics:
    This class is designed to be used together with startup/shutdown hooks of a web framework, specifically the lifespan function in FastAPI
    As such before any other method of this class can be used the open() method should be called and before shutdown the close() method should be called
    many private methods (prefixed with __) do not create or close transactions (since they do not get their own connection). Instead other non-private methods are expected to lend them their connection. This way we can combine the same sql queries in multiple transactions while reusing the same code
    """

    hasher = PasswordHasher()

    @abstractmethod
    def __init__(self, connection_string: str) -> None:
        pass

    def _encrypt(
        self, secret_key: SecretKeyValidated | None = None
    ) -> tuple[str, Callable[[bytes], bytes]]:
        if secret_key is None:
            secret_key = dp.config.security.secret_key
        nonce_rfc7539 = get_random_bytes(12)
        cipher = ChaCha20.new(
            key=bytes.fromhex(secret_key.root.get_secret_value()), nonce=nonce_rfc7539
        )
        nonce_str = b64encode(nonce_rfc7539).decode("utf-8")

        def ciphertext_generator(plaintext: bytes):
            return cipher.encrypt(plaintext)

        return (nonce_str, ciphertext_generator)

    def _decrypt(
        self, nonce: str, secret_key: SecretKeyValidated | None = None
    ) -> Callable[[bytes], bytes]:
        if secret_key is None:
            secret_key = dp.config.security.secret_key
        cipher = ChaCha20.new(
            key=bytes.fromhex(secret_key.root.get_secret_value()), nonce=b64decode(nonce)
        )

        def plaintext_generator(ciphertext: bytes):
            return cipher.decrypt(ciphertext)

        return plaintext_generator

    @abstractmethod
    async def open(self):
        """
        This method should initiate the database connection(s), make sure the schema, tables and metadata exist and are valid by creating them if missing or migrating them from an older version. All of that should be part of a single transaction. This method will be called before any other of this class.
        """
        pass

    @abstractmethod
    async def close(self):
        """
        This method should close all transactions and connections and do anything else that needs to be done before the application can safely shutdown. This method will be called last after any other of this class.
        """
        pass

    @abstractmethod
    async def _add_local_user_hashed(
        self,
        email: EmailValidated,
        hashed_password: str,
        is_admin: bool = False,
        is_verified: bool = False,
    ) -> int | None:
        """
        This method creates a new user if it doesn't exist yet. The password is already hashed here which is the reason why this method is protected. It should not be used outside of this base class, use add_new_user instead!
        Returns the user id of the created user
        Returns None if a user with this email already exists.
        """
        pass

    async def add_local_user(
        self,
        email: EmailValidated,
        password: PasswordValidated,
        is_admin: bool = False,
        is_verified: bool = False,
    ) -> int | None:
        """
        Create a new user if it doesn't exist yet. The password will be hashed in this function before writing it in the database.
        Returns the user id of the created user
        Returns None if a user with this email already exists.
        """
        hashed_password = self.hasher.hash(password.root.get_secret_value())
        return await self._add_local_user_hashed(email, hashed_password, is_admin, is_verified)

    @abstractmethod
    async def _ensure_local_user_is_provisioned_hashed(
        self,
        provision_number: int,
        email: EmailValidated,
        hashed_password: str,
        is_admin: bool = False,
    ) -> int:
        """
        This method provisions a user from the config file. Compared to add_local_user it also checks the provision_number and changes the users email, password and admin privileges if they have changed.
        Returns the user id of the user
        """
        pass

    async def ensure_local_user_is_provisioned(
        self,
        provision_number: int,
        email: EmailValidated,
        password: SecretStr,
        is_admin: bool = False,
    ) -> int:
        """
        Provision a user from the config file. The password will be hashed in this function before writing it in the database.
        Returns the user id of the user
        """
        hashed_password = self.hasher.hash(password.get_secret_value())
        return await self._ensure_local_user_is_provisioned_hashed(
            provision_number, email, hashed_password, is_admin
        )

    @abstractmethod
    async def ensure_oidc_user_exists(self, iss: str, sub: str, email: EmailValidated) -> int:
        """
        Add a database entry for an oidc user if it doesn't exist yet. Will be called at first login of this user.
        If the user exists but with a different email then update the email address of the user.
        Returns the user id of the created user
        """
        pass

    @abstractmethod
    async def ensure_ldap_user_exists(
        self, provider_name: str, uid: str, email: EmailValidated
    ) -> int:
        """
        Add a database entry for an ldap user if it doesn't exist yet. Will be called at first login of this user.
        If the user exists but with a different email then update the email address of the user.
        Returns the user id of the user
        """
        pass

    @abstractmethod
    async def _add_new_user_token_hashed_encrypted(
        self,
        user_id: int,
        name: str,
        token_hash: str,
        explicit: bool = False,
        admin_privileges: bool = False,
        expires_at: datetime | None = None,
        oidc_refresh_token_id: int | None = None,
        encrypted_oidc_refresh_token: bytes | None = None,
        nonce: str | None = None,
    ):
        """
        Add hash of new token to database
        """
        pass

    @abstractmethod
    async def _rotate_user_token_hashed(
        self, token_id: int, token_hash: str, expires_at: datetime | None = None
    ):
        pass

    async def add_new_user_token(
        self,
        user_id: int,
        name: str,
        explicit: bool = False,
        admin_privileges: bool = False,
        expiration_time_minutes: int | None = None,
        oidc_refresh_token_id: int | None = None,
        oidc_refresh_token: SecretStr | None = None,
    ) -> Token:
        """
        Create new user token and returns it
        """
        token = Token()
        token_hash = token.hash()

        # Sanity check to ensure that the token and its hash are unique.
        while (await self._get_user_by_token_hashed(token_hash)) is not None:
            token = Token()
            token_hash = token.hash()

        if expiration_time_minutes is not None:
            expires_at = minutes_from_now_to_datetime(expiration_time_minutes)
        else:
            expires_at = None

        if oidc_refresh_token is not None:
            nonce, ciphertext_generator = self._encrypt()
            oidc_refresh_token_enc = ciphertext_generator(
                oidc_refresh_token.get_secret_value().encode("utf-8")
            )
        else:
            nonce, oidc_refresh_token_enc = None, None

        await self._add_new_user_token_hashed_encrypted(
            user_id,
            name,
            token_hash,
            explicit,
            admin_privileges,
            expires_at,
            oidc_refresh_token_id,
            oidc_refresh_token_enc,
            nonce,
        )
        return token

    async def rotate_user_token(
        self, token_id: int, expiration_time_minutes: int | None = None
    ) -> Token:
        """
        Rotates the token_hash of an existing token without changing it's other parameters
        """
        token = Token()
        token_hash = token.hash()

        # Sanity check to ensure that the token and its hash are unique.
        while (await self._get_user_by_token_hashed(token_hash)) is not None:
            token = Token()
            token_hash = token.hash()

        if expiration_time_minutes is not None:
            expires_at = minutes_from_now_to_datetime(expiration_time_minutes)
        else:
            expires_at = None

        await self._rotate_user_token_hashed(token_id, token_hash, expires_at)
        return token

    @abstractmethod
    async def delete_user(self, user_id: int):
        """
        Delete the user with id user_id.
        """
        pass

    @abstractmethod
    async def delete_token_of_user(self, user_id: int, token_id: int):
        """
        Delete the token secret with id token_id if it belongs to user with id user_id.
        """
        pass

    @abstractmethod
    async def delete_all_tokens_of_user(self, user_id: int):
        """
        Delete all token secrets of user with id user_id
        """
        pass

    @abstractmethod
    async def delete_jobs_of_user(self, user_id: int, job_ids: list[int]):
        """
        Delete all provided jobs of the provided user
        """
        pass

    @abstractmethod
    async def delete_runner(self, runner_id: int):
        """
        Deletes the runner with id runner_id thus invalidating it's access token
        """
        pass

    @abstractmethod
    async def _get_user_by_token_hashed(
        self, token_hash: str
    ) -> tuple[LocalUserInternal | OidcUserInternal | LdapUserInternal, TokenInfoInternal] | None:
        pass

    async def get_user_by_token(
        self, token: Token
    ) -> tuple[LocalUserInternal | OidcUserInternal | LdapUserInternal, TokenInfoInternal] | None:
        """
        Validates the provided token and returns the associated user if valid (first tuple value)
        and the amount of minutes until the token will be expired.
        Also sets the last_usage field of that token to now.
        Returns None if the token isn't valid anymore.
        """
        return await self._get_user_by_token_hashed(token.hash())

    @abstractmethod
    async def get_user_by_id(
        self, user_id: int
    ) -> LocalUserInternal | OidcUserInternal | LdapUserInternal | None:
        """
        Return the user with the specified id, regardless of whether this user is a local, oidc or ldap user.
        Return None if no such user exists
        """
        pass

    @abstractmethod
    async def accept_tos_of_user(self, user_id: int, tos_id: int, tos_version: int):
        """
        Marks the term of service with the specified id and version as accepted for the user with the specified id
        """
        pass

    @abstractmethod
    async def get_local_user_by_email(self, email: EmailValidated) -> LocalUserInternal | None:
        """
        Return a local user with the matching email, or None if the email doesn't match any user
        """
        pass

    @abstractmethod
    async def get_oidc_user_by_iss_sub(self, iss: str, sub: str) -> OidcUserInternal | None:
        """
        Return an oidc user with the matching iss/sub pair, or None if iss/sub doesn't match any user
        """
        pass

    @abstractmethod
    async def get_oidc_user_by_id(self, user_id: int) -> OidcUserInternal | None:
        """
        Return an oidc user with the matching user id, or None if user_id doesn't match any user
        """
        pass

    @abstractmethod
    async def get_ldap_user_by_id(self, user_id: int) -> LdapUserInternal | None:
        """
        Return an ldap user with the matching user id, or None if user_id doesn't match any user
        """
        pass

    @abstractmethod
    async def get_info_of_all_tokens_of_user(self, user_id: int) -> list[TokenInfoInternal]:
        """
        Return a list of all stripped token secret objects a user has
        """
        pass

    @abstractmethod
    async def get_all_user_emails(self) -> list[EmailValidated]:
        """
        Returns a list of all email addresses of all Project-W users, regardless of whether they are local, oidc or ldap users
        """
        pass

    @abstractmethod
    async def _update_password_hash(self, user_id: int, new_password_hash: str):
        """
        Sometimes passwords need rehashing. This function implements that.
        It gets called every time a password for a user is checked in 'get_user_by_email_checked_password'
        """
        pass

    async def get_local_user_by_email_checked_password(
        self, email: EmailValidated, password: SecretStr
    ) -> LocalUserInternal | None:
        """
        Return a user with the matching email if the provided password is also correct, or None if the email doesn't match any user or the password is incorrect
        Basically 'get_local_user_by_email' with builtin password checker
        """
        user = await self.get_local_user_by_email(email)
        if user is None:
            return None

        try:
            self.hasher.verify(user.password_hash, password.get_secret_value())
        except VerificationError:
            return None

        if self.hasher.check_needs_rehash(user.password_hash):
            await self._update_password_hash(user.id, self.hasher.hash(password.get_secret_value()))

        return user

    @abstractmethod
    async def verify_local_user(self, user_id: int, new_email: EmailValidated):
        """
        Update a local user to be verified.
        Update the email address to new_email as well.
        """
        pass

    @abstractmethod
    async def _update_local_user_password(
        self,
        email: EmailValidated,
        hashed_new_password: str,
    ) -> int | None:
        """
        This method updates the password of an existing local user.
        Returns the user id of the user whose password was updated.
        Returns None if no user with this email exists.
        """
        pass

    async def update_local_user_password(
        self,
        email: EmailValidated,
        new_password: PasswordValidated,
    ) -> int | None:
        """
        Create a new user if it doesn't exist yet. The password will be hashed in this function before writing it in the database.
        Returns the user id of the created user
        Returns None if a user with this email already exists.
        """
        hashed_new_password = self.hasher.hash(new_password.root.get_secret_value())
        return await self._update_local_user_password(email, hashed_new_password)

    @abstractmethod
    async def _create_runner_hashed(self, token_hash: str) -> int:
        """
        INSERT a new runner with the provided token_hash into the database
        Return the id of the newly created runner
        """
        pass

    @abstractmethod
    async def add_new_job_settings(
        self, user_id: int, job_settings: JobSettingsResponse, is_new_default: bool = False
    ) -> int:
        """
        Create a new set of job settings for the user with id user_id and return its id
        If is_new_default is true then make that new set of settings the new default for this user
        """
        pass

    @abstractmethod
    async def get_default_job_settings_of_user(self, user_id: int) -> JobSettingsResponse | None:
        """
        Return the job settings of that user that are marked as the default
        Returns None if the user doesn't have any default settings
        """
        pass

    @abstractmethod
    async def get_job_settings_by_job_id(self, job_id: int) -> JobSettingsResponse | None:
        """
        Return the job settings that are the job with the job id job_id has
        Returns None if no job with id job_id exists
        """
        pass

    @abstractmethod
    async def add_new_job(
        self, user_id: int, audio_file: UploadFile, job_settings_id: int | None = None
    ) -> int | None:
        """
        Create a new job of the provided user and with the provided audio file and job settings
        If provided job_settings_id is None then use the default job settings of that user
        Returns None if the user doesn't own job_settings with id job_settings_id
        """
        pass

    @abstractmethod
    async def get_total_number_of_jobs_of_user(
        self, user_id: int, excl_finished: bool, excl_downloaded: bool
    ) -> int:
        """
        Get total number of jobs that a user has (after optionally excluding finished and/or downloaded jobs)
        """
        pass

    @abstractmethod
    async def get_job_ids_of_user(
        self,
        user_id: int,
        start_index: int,
        end_index: int,
        sort_key: JobSortKey,
        desc: bool,
        excl_finished: bool,
        excl_downloaded: bool,
    ) -> list[int]:
        """
        Query the first k jobs sorted by sort_key (descending if desc = True, ascending otherwise) of user_id
        """
        pass

    @abstractmethod
    async def get_job_by_id(self, job_id) -> JobInternal | None:
        """
        Returns the job matching the provided job_id.
        Returns None if no job with that id exists
        """
        pass

    @abstractmethod
    async def get_job_infos_of_user(self, user_id: int, job_ids: list[int]) -> list[JobInternal]:
        """
        Returns all job metadata (excluding job settings, audio/audio oid, user_id, job_settings_id and transcript) for all provided job ids that belong to the provided user_id
        """
        pass

    @abstractmethod
    async def get_job_infos_with_settings_of_user(
        self, user_id: int, job_ids: list[int]
    ) -> list[JobAndSettingsInternal]:
        """
        Returns all job metadata (including job settings but excluding audio/audio oid, user_id, job_settings_id and transcript) for all provided job ids that belong to the provided user_id
        """
        pass

    @abstractmethod
    async def get_job_audio(self, job_id: int) -> AsyncGenerator[bytes, None]:
        """
        This is a python generator that returns chunks of the binary file (in __file_chunk_size_in_bytes increments)
        Use this like any other python async generator (i.e. file open-like interface)
        Binary is only read from database one chunk at a time instead of the whole file at once.
        """
        yield b""  # type annotation needs this for some reason even though this is an abstract method

    @abstractmethod
    async def get_job_transcript_of_user_set_downloaded(
        self, user_id: int, job_id: int, transcript_type: TranscriptTypeEnum
    ) -> str | dict | None:
        """
        Returns the transcript object of a finished job belonging to that user
        Returns None if no job with that id exists or if that job doesn't have a transcript (yet)
        """
        pass

    @abstractmethod
    async def get_all_unfinished_jobs(self) -> list[tuple[int, int, bool]]:
        """
        Returns the job id, user id and whether the job was marked for aborting of all jobs in the database that haven't finished yet (not downloaded, finished, failed).
        Will be called at startup to enqueue existing jobs
        """
        pass

    @abstractmethod
    async def get_user_id_of_job(self, job_id: int) -> int | None:
        """
        Returns the user_id of the user that owns a specific job.
        Returns None if no job with job_id exists
        """
        pass

    @abstractmethod
    async def mark_job_as_aborting(self, job_id: int):
        """
        Marks a job as being aborted. This will be signaled to the runner. A aborted job will then be finished as failed
        Does nothing if no job with that id exists
        """
        pass

    @abstractmethod
    async def finish_successful_job(self, runner: OnlineRunner, transcript: Transcript):
        """
        Mark the job with id job_id as successfully finished by adding all the submitted information to it (if that job exists)
        """
        pass

    @abstractmethod
    async def finish_failed_job(
        self, job_id: int, error_msg: str, runner: OnlineRunner | None = None
    ):
        """
        Mark the job with id job_id as unsuccessfully finished by adding all the submitted information to it (if that job exists).
        Note that in contrary to finish_successful_job here OnlineRunner is optional because the job might have failed before it was assigned to a runner (e.g. if it was aborted)
        """
        pass

    @abstractmethod
    async def _get_runner_by_token_hashed(self, token_hash: str) -> int | None:
        """
        Returns the id of the runner with the matching token_hash
        Returns None if no Runner matches
        """
        pass

    async def create_runner(self) -> RunnerCreatedResponse:
        """
        Creates a new runner, inserts it into the database and returns its newly generated token
        token generation is done here because it is important that the token is sever generated (because of how it is hashed)
        """
        token = Token()
        token_hash = token.hash()

        # Sanity check to ensure that the token and its hash are unique.
        while (await self._get_runner_by_token_hashed(token_hash)) is not None:
            token = Token()
            token_hash = token.hash()

        runner_id = await self._create_runner_hashed(token_hash)
        return RunnerCreatedResponse(id=runner_id, token=token.root.get_secret_value())

    async def get_runner_by_token(self, token: Token) -> int | None:
        return await self._get_runner_by_token_hashed(token.hash())

    @abstractmethod
    async def general_cleanup(self):
        """
        Generic cleanup of database entries like orphaned rows and the like
        """
        pass

    @abstractmethod
    async def user_cleanup(self, retention_time_in_days: int):
        """
        Cleanup of users and their data who haven't logged in the specified retention time.
        """
        pass

    @abstractmethod
    async def job_cleanup(self, retention_time_in_days: int):
        """
        Cleanup of jobs and their data (most notably transcripts!) which are older than the specified retention time (after finishing time, not creation time)
        """
        pass

    @abstractmethod
    async def get_ldap_tokens(self) -> list[LdapTokenInfoInternal]:
        """
        Get all tokens associated with LDAP users and their LDAP user attributes
        """
        pass

    @abstractmethod
    async def get_oidc_tokens(self) -> list[OidcTokenInfoInternal]:
        """
        Get all tokens associated with OIDC users and their OIDC user attributes
        """
        pass

    @abstractmethod
    async def get_oidc_refresh_token_of_token(self, token_id: int) -> SecretStr | None:
        """
        Get the oidc refresh token associated with an auth token
        Returns None if no auth token with id token_id exists or if that auth token doesn't have an oidc refresh token associated with it
        """
        pass

    @abstractmethod
    async def replace_oidc_refresh_token(self, oidc_refresh_token_id, new_refresh_token: SecretStr):
        """
        OIDC returned a new refresh_token, so replace the stored refresh_token for id oidc_refresh_token_id with a new one
        """
        pass

    @abstractmethod
    async def add_site_banner(self, urgency: int, html: str) -> int | None:
        """
        Creates a new site banner. Returns it's id if Successful, returns None if not
        """
        pass

    @abstractmethod
    async def list_site_banners(self) -> list[SiteBannerResponse]:
        """
        List all site banners
        """
        pass

    @abstractmethod
    async def delete_site_banner(self, id: int):
        """
        List site banner with provided id
        """
        pass

    @abstractmethod
    async def rotate_secret_key(self, new_secret_key: SecretKeyValidated):
        """
        Re-encrypts all encrypted database contents with the new secret key
        """
        pass

    @abstractmethod
    async def delete_encrypted_content(self):
        """
        Deletes all encrypted content from the database
        """
        pass


class PostgresAdapter(DatabaseAdapter):
    apool: AsyncConnectionPool
    schema: LiteralString = "project_w"
    minimal_required_postgres_version = 14  # for both postgres and libpq
    __file_chunk_size_in_bytes = 10240  # 10MiB

    def __init__(self, connection_string: str) -> None:
        self.apool = AsyncConnectionPool(
            conninfo=connection_string,
            open=False,
            check=AsyncConnectionPool.check_connection,
        )

    async def open(self):
        dp.logger.info("Trying to connect to PostgreSQL database...")

        # make sure libpq version is at least the minimal supported one. Do this first because it doesn't even require connecting to the database
        self.__ensure_psycopg_libpq_version()

        await self.apool.open()
        await self.apool.wait()  # this waits until all connections are established. This makes sure that the backend can properly talk to the database before it accepts clients
        dp.logger.info("Successfully connected to database. Preparing database now...")

        async with self.apool.connection() as conn:
            # get lock first to prevent race conditions. Otherwise multiple workers/pods would try to create the database/tables at the same time which would throw errors due to violated uniqueness constraints once the first worker/pod commits their transaction
            await self.__acquire_advisory_lock(conn)

            # ensure that the postgresql database version is at least the minimal supported one
            await self.__ensure_postgresql_version(conn)

            table_names: list[LiteralString] = [
                "site_data",
                "users",
                "local_accounts",
                "oidc_accounts",
                "ldap_accounts",
                "runners",
                "job_settings",
                "jobs",
                "transcripts",
                "tokens",
            ]

            if not await self.__check_schema_exists(conn, self.schema):
                await self.__create_schema(conn, self.schema)

                # also create all other tables to skip their existence checks and migration code since there can't be any tables in a newly created schema anyway
                await self.__create_all_tables(conn)
            elif not await self.__check_table_exists(conn, "metadata"):
                # if the metadata is missing we can't know whether and how to do a migration on the other table. Because of this we throw an exception if the metadata table is missing but any other table exists to be safe
                for table_name in table_names:
                    if await self.__check_table_exists(conn, table_name):
                        raise Exception(
                            f"Critical: The metadata table is missing but the {table_name} table still exists. Either restore the metadata table with its previous contents or drop the {table_name} table!"
                        )

                # if all tables where missing but the schema existed for some reason then ignore that and just create all the tables in that schema
                await self.__create_all_tables(conn)
            else:
                # if schema and metadata table already existed we still have to check if all the other tables still exist
                # no table can be dropped on its own
                # do database migration before creating any missing tables because those created tables would have the new format which would brick the migration code
                await self.__update_database_schema_if_needed(conn)

                for table_name in table_names:
                    if not await self.__check_table_exists(conn, table_name):
                        raise Exception(
                            f"Critical: The metadata table exists but the {table_name} table is missing. Either restore the {table_name} table with its previous contents or drop the metadata table as well!"
                        )

                # check when the last cleanups were performed and warn the user if it was too long ago
                await self.__warn_about_missing_cleanups()

        dp.logger.info("Database is ready to use")

    async def close(self):
        dp.logger.info("Closing PostgreSQL connections...")
        await self.apool.close()

    async def __acquire_advisory_lock(self, conn: AsyncConnection):
        async with conn.cursor(row_factory=scalar_row) as cur:
            await cur.execute(
                """
                    SELECT pg_advisory_xact_lock(42);
                """
            )

    async def __ensure_postgresql_version(self, conn: AsyncConnection):
        async with conn.cursor(row_factory=scalar_row) as cur:
            await cur.execute(
                """
                SHOW server_version
            """
            )
            version_string = await cur.fetchone()
            if not version_string:
                raise Exception("Couldn't read the version of the specified PostgreSQL database")
            if int(version_string[0:2]) < self.minimal_required_postgres_version:
                raise Exception(
                    f"The version of the specified PostgreSQL database is {version_string} while the minimal required version is {self.minimal_required_postgres_version}"
                )
            dp.logger.info(f"PostgreSQL server is on version {version_string}")

    def __ensure_psycopg_libpq_version(self):
        version_string = pq.version()
        if int(str(version_string)[0:2]) < self.minimal_required_postgres_version:
            raise Exception(
                f"The version of libpq loaded by psycopg is {version_string} while the minimal required version is {self.minimal_required_postgres_version}"
            )
        dp.logger.info(f"PostgreSQL libpq is on version {version_string}")

    async def __check_schema_exists(
        self, conn: AsyncConnection, schema_name: LiteralString
    ) -> bool:
        dp.logger.info(f"Checking if schema {schema_name} exists...")
        async with conn.cursor(row_factory=scalar_row) as cur:
            await cur.execute(
                f"""
                SELECT EXISTS (
                    SELECT *
                    FROM information_schema.schemata
                    WHERE
                        schema_name = '{schema_name}'
                )
            """
            )
            exists = await cur.fetchone()
            if exists is not None:
                return exists
            else:
                return False

    async def __check_table_exists(self, conn: AsyncConnection, table_name: LiteralString):
        dp.logger.info(f"Checking if {table_name} table exists...")
        async with conn.cursor(row_factory=scalar_row) as cur:
            await cur.execute(
                f"""
                SELECT EXISTS (
                    SELECT *
                    FROM information_schema.tables
                    WHERE
                        table_schema = '{self.schema}' AND
                        table_type = 'BASE TABLE' AND
                        table_name = '{table_name}'
                )
            """
            )
            exists = await cur.fetchone()
            if exists is not None:
                return exists
            else:
                return False

    async def __create_schema(self, conn: AsyncConnection, schema_name: LiteralString):
        dp.logger.info(f"Schema {schema_name} was missing. Creating it now...")
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                CREATE SCHEMA {schema_name}
            """
            )

    async def __create_all_tables(self, conn: AsyncConnection):
        """
        All tables (except for the jobs table, see its creation function) need to created at the same time because of foreign key constraints
        Nothing references the metadata table however we cannot support tables existing without it because the application gets the used schema version from the metadata table
        """
        async with conn.cursor() as cur:
            dp.logger.info("Creating metadata table...")
            await cur.execute(
                f"""
                CREATE TABLE {self.schema}.metadata (
                    topic text,
                    data jsonb NOT NULL,
                    PRIMARY KEY (topic)
                )
            """
            )
            # also init all used topics in table so that they are always defined
            application_metadata = {
                "last_used_version": version,
                "last_used_version_tuple": version_tuple,
            }
            await cur.execute(
                f"""
                INSERT INTO {self.schema}.metadata (topic, data)
                VALUES ('application', %s)
            """,
                [Jsonb(application_metadata)],
            )
            cleanup_metadata = {
                "general_last_cleanup": datetime.min.isoformat(),
                "jobs_last_cleanup": datetime.min.isoformat(),
                "users_last_cleanup": datetime.min.isoformat(),
            }
            await cur.execute(
                f"""
                INSERT INTO {self.schema}.metadata (topic, data)
                VALUES ('cleanup', %s)
            """,
                [Jsonb(cleanup_metadata)],
            )

            dp.logger.info("Creating users table...")
            await cur.execute(
                f"""
                CREATE TABLE {self.schema}.users (
                    id int GENERATED ALWAYS AS IDENTITY,
                    accepted_tos jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                    primary key (id)
                )
            """
            )

            dp.logger.info("Creating site_data table...")
            await cur.execute(
                f"""
                CREATE TABLE {self.schema}.site_data (
                    id int GENERATED ALWAYS AS IDENTITY,
                    type text NOT NULL,
                    urgency int NOT NULL,
                    html text NOT NULL
                )
            """
            )

            dp.logger.info("Creating tokens table...")
            await cur.execute(
                f"""
                CREATE TABLE {self.schema}.oidc_refresh_tokens (
                    id int GENERATED ALWAYS AS IDENTITY,
                    encrypted_token bytea NOT NULL,
                    nonce text NOT NULL CHECK(length(nonce) = 16),
                    PRIMARY KEY (id)
                )
                """
            )
            await cur.execute(
                f"""
                CREATE TABLE {self.schema}.tokens (
                    id int GENERATED ALWAYS AS IDENTITY,
                    token_hash text UNIQUE CHECK(length(token_hash) = 43),
                    user_id int NOT NULL,
                    name text NOT NULL,
                    explicit boolean NOT NULL DEFAULT false,
                    admin_privileges boolean NOT NULL DEFAULT false,
                    expires_at timestamptz,
                    last_usage timestamptz NOT NULL DEFAULT NOW(),
                    oidc_refresh_token_id int,
                    PRIMARY KEY (id),
                    FOREIGN KEY (user_id) REFERENCES {self.schema}.users (id) ON DELETE CASCADE,
                    FOREIGN KEY (oidc_refresh_token_id) REFERENCES {self.schema}.oidc_refresh_tokens (id) ON DELETE CASCADE
                )
            """
            )

            dp.logger.info("Creating local_accounts table...")
            # according to https://www.rfc-editor.org/errata/eid1003 the upper limit for mail address forward paths is 256 octets (2 of which are angle brackets and thus not part of the address itself). When using UTF-8 this might translate in even less characters, but is still a good upper limit
            await cur.execute(
                f"""
                CREATE TABLE {self.schema}.local_accounts (
                    email varchar(254) NOT NULL,
                    id int NOT NULL UNIQUE,
                    password_hash text NOT NULL CHECK(length(password_hash) = 97),
                    is_admin boolean NOT NULL DEFAULT false,
                    is_verified boolean NOT NULL DEFAULT false,
                    provision_number int UNIQUE DEFAULT null,
                    PRIMARY KEY (email),
                    FOREIGN KEY (id) REFERENCES {self.schema}.users (id) ON DELETE CASCADE
                )
            """
            )

            dp.logger.info("Creating oidc_accounts table...")
            await cur.execute(
                f"""
                CREATE TABLE {self.schema}.oidc_accounts (
                    iss text NOT NULL,
                    sub text NOT NULL,
                    id int NOT NULL UNIQUE,
                    email varchar(254) NOT NULL,
                    PRIMARY KEY (iss, sub),
                    FOREIGN KEY (id) REFERENCES {self.schema}.users (id) ON DELETE CASCADE
                )
            """
            )

            dp.logger.info("Creating ldap_accounts table...")
            await cur.execute(
                f"""
                CREATE TABLE {self.schema}.ldap_accounts (
                    provider_name text NOT NULL,
                    uid text NOT NULL,
                    id int NOT NULL UNIQUE,
                    email varchar(254) NOT NULL,
                    PRIMARY KEY (provider_name, uid),
                    FOREIGN KEY (id) REFERENCES {self.schema}.users (id) ON DELETE CASCADE
                )
            """
            )

            dp.logger.info("Creating runners table...")
            await cur.execute(
                f"""
                CREATE TABLE {self.schema}.runners (
                    id int GENERATED ALWAYS AS IDENTITY,
                    token_hash text UNIQUE CHECK(length(token_hash) = 43),
                    PRIMARY KEY (id)
                )
            """
            )

            dp.logger.info("Creating job_settings table...")
            await cur.execute(
                f"""
                CREATE TABLE {self.schema}.job_settings (
                    id int GENERATED ALWAYS AS IDENTITY,
                    user_id int NOT NULL,
                    is_default bool NOT NULL DEFAULT false,
                    settings jsonb NOT NULL,

                    PRIMARY KEY (id),
                    FOREIGN KEY (user_id) REFERENCES {self.schema}.users (id) ON DELETE CASCADE
                )
                """
            )
            # make sure that there can only be one setting set as default per user
            await cur.execute(
                f"""
                CREATE UNIQUE INDEX only_one_default_setting_per_user
                ON {self.schema}.job_settings (user_id)
                WHERE is_default
                """
            )

            dp.logger.info("Creating jobs table")
            await cur.execute(
                f"""
                CREATE TABLE {self.schema}.jobs (
                    id int GENERATED ALWAYS AS IDENTITY,
                    user_id int NOT NULL,
                    job_settings_id int,
                    creation_timestamp timestamptz NOT NULL DEFAULT NOW(),
                    file_name text NOT NULL,
                    aborting boolean NOT NULL DEFAULT false,
                    audio_oid oid,
                    nonce text CHECK(nonce IS NULL OR length(nonce) = 16),
                    finish_timestamp timestamptz,
                    runner_name varchar(40),
                    runner_id int,
                    runner_version text,
                    runner_git_hash varchar(40),
                    runner_source_code_url text,
                    downloaded boolean,
                    error_msg text,
                    PRIMARY KEY (id),
                    FOREIGN KEY (user_id) REFERENCES {self.schema}.users (id) ON DELETE CASCADE,
                    FOREIGN KEY (job_settings_id) REFERENCES {self.schema}.job_settings (id) ON DELETE CASCADE,
                    FOREIGN KEY (runner_id) REFERENCES {self.schema}.runners (id) ON DELETE SET NULL,
                    CONSTRAINT only_finished_job_can_have_runner CHECK (
                        (finish_timestamp IS NULL AND runner_id IS NULL AND runner_name IS NULL AND runner_version IS NULL AND runner_git_hash IS NULL AND runner_source_code_url IS NULL)
                        OR finish_timestamp IS NOT NULL
                    ),
                    CONSTRAINT either_no_or_all_runner_info_except_runner_id CHECK (
                        (runner_id IS NULL AND runner_name IS NULL AND runner_version IS NULL AND runner_git_hash IS NULL AND runner_source_code_url IS NULL)
                        OR (runner_name IS NOT NULL AND runner_version IS NOT NULL AND runner_git_hash IS NOT NULL AND runner_source_code_url IS NOT NULL)
                    ),
                    CONSTRAINT only_finished_job_is_succeeded_or_failed CHECK (
                        (finish_timestamp IS NOT NULL AND downloaded IS NOT NULL AND error_msg IS NULL)
                        OR (finish_timestamp IS NOT NULL AND downloaded IS NULL AND error_msg IS NOT NULL)
                        OR (finish_timestamp IS NULL AND downloaded IS NULL AND error_msg IS NULL)
                    ),
                    CONSTRAINT finished_job_has_no_audio_oid CHECK (
                        (finish_timestamp IS NOT NULL AND audio_oid IS NULL)
                        OR (finish_timestamp IS NULL)
                    ),
                    CONSTRAINT every_job_with_oid_has_nonce CHECK(
                        (audio_oid IS NULL AND nonce IS NULL)
                        OR (audio_oid IS NOT NULL AND nonce IS NOT NULL)
                    ),
                    CONSTRAINT aborting_job_has_no_audio_oid_and_is_not_finished CHECK (
                        (NOT aborting)
                        OR (aborting AND audio_oid IS NULL AND finish_timestamp IS NULL)
                    )
                )
            """
            )
            # user_id is neither primary key nor unique, so we have to create an index for it manually.
            # this makes sense since queries where we want to get jobs of a user are very common, and because else the ON DELETE CASCADE would be very expensive
            await cur.execute(
                f"""
                CREATE INDEX ON {self.schema}.jobs (user_id)
            """
            )
            # make sure that large objects containing the audio will be deleted when the job gets deleted (also on cascade, e.g. when a user gets deleted)
            await cur.execute(
                """
                CREATE OR REPLACE FUNCTION deleteaudio() RETURNS TRIGGER AS $$
                BEGIN
                    PERFORM lo_unlink(OLD.audio_oid);
                    RETURN NULL;
                END;
                $$ LANGUAGE plpgsql
                """
            )
            await cur.execute(
                f"""
                CREATE TRIGGER delete_audio
                AFTER DELETE ON {self.schema}.jobs
                FOR EACH ROW
                WHEN (OLD.audio_oid IS NOT NULL)
                EXECUTE FUNCTION deleteaudio()
                """
            )

            dp.logger.info("Creating transcripts table")
            await cur.execute(
                f"""
                CREATE TABLE {self.schema}.transcripts (
                    job_id int,
                    as_txt bytea NOT NULL,
                    as_txt_nonce text NOT NULL CHECK(length(as_txt_nonce) = 16),
                    as_srt bytea NOT NULL,
                    as_srt_nonce text NOT NULL CHECK(length(as_srt_nonce) = 16),
                    as_tsv bytea NOT NULL,
                    as_tsv_nonce text NOT NULL CHECK(length(as_tsv_nonce) = 16),
                    as_vtt bytea NOT NULL,
                    as_vtt_nonce text NOT NULL CHECK(length(as_vtt_nonce) = 16),
                    as_json bytea NOT NULL,
                    as_json_nonce text NOT NULL CHECK(length(as_json_nonce) = 16),
                    PRIMARY KEY (job_id),
                    FOREIGN KEY (job_id) REFERENCES {self.schema}.jobs (id) ON DELETE CASCADE
                )
                """
            )

    async def __update_database_schema_if_needed(self, conn: AsyncConnection):
        dp.logger.info("Checking application version in database metadata...")
        async with conn.cursor(row_factory=scalar_row) as cur:
            # first check if last_used_version is newer than the current version
            # this would mean that another instance of the application (e.g. another Kubernetes pod) is running on a newer version than this one
            # this is common when updating Kubernetes deployments since pods will be taken down and updated one-by-one for zero downtime
            # this of course can only be done if there are no breaking changes in the database with the newer version. This needs to be checked here in the future when we introduce such changes
            await cur.execute(
                f"""
                SELECT data
                FROM {self.schema}.metadata
                WHERE topic = 'application'
            """
            )
            application_metadata = await cur.fetchone()
            if (
                application_metadata is not None
                and (db_version_tuple := application_metadata.get("last_used_version_tuple"))
                is not None
                and (db_version := application_metadata.get("last_used_version")) is not None
            ):
                parsed_version_tuple = parse_version_tuple(version_tuple)
                parsed_db_version_tuple = parse_version_tuple(db_version_tuple)
                min_db_version = parse_version_tuple((0, 3, 0))
                if parsed_db_version_tuple < min_db_version:
                    raise Exception(
                        f"Your database was from Project-W version {db_version}, we only support database migration beginning from version {min_db_version}"
                    )
                if parsed_version_tuple > parsed_db_version_tuple:
                    # TODO: Add database migration code here once it becomes necessary after a future update
                    if parsed_db_version_tuple < parse_version_tuple((0, 4, 0)):  # in version 0.3.x
                        raise Exception(
                            "No database migration code available for database coming from Project-W versions lower than 0.4.0"
                        )
                    await cur.execute(
                        f"""
                        UPDATE {self.schema}.metadata
                        SET data = jsonb_set(
                            jsonb_set(
                                data,
                                '{{last_used_version}}',
                                %s
                            ),
                            '{{last_used_version_tuple}}',
                            %s
                        )
                        WHERE topic = 'application'
                    """,
                        (Jsonb(version), Jsonb(version_tuple)),
                    )
                    dp.logger.info(f"Database successfully migrated to version {version}")

                elif parsed_version_tuple < parsed_db_version_tuple:
                    if parsed_version_tuple[0] < parsed_db_version_tuple[0]:
                        # there is another application instance running that runs on a newer major version. Abort!
                        raise Exception(
                            f"The database has been converted to application version {db_version} while this application only runs on version {version}. Downgrading between major versions is not supported!"
                        )

                else:
                    dp.logger.info("No database schema migration required")

    async def __warn_about_missing_cleanups(self):
        async with self.apool.connection() as conn:
            async with conn.cursor() as cur:
                dp.logger.info("Checking cleanup metadata...")
                await cur.execute(
                    f"""
                        SELECT data['general_last_cleanup'], data['users_last_cleanup'], data['jobs_last_cleanup']
                        FROM {self.schema}.metadata
                        WHERE topic = 'cleanup'
                    """
                )
                cleanup_info = await cur.fetchone()
                if cleanup_info is None:
                    raise Exception(
                        "Database doesn't contain cleanup timestamps in the metadata table!"
                    )
                (
                    general_last_cleanup_isoformat,
                    users_last_cleanup_isoformat,
                    jobs_last_cleanup_isoformat,
                ) = cleanup_info
                general_last_cleanup = datetime.fromisoformat(general_last_cleanup_isoformat)
                time_since_general_last_cleanup = datetime.now() - general_last_cleanup
                # check for 25h instead of 24h to give cron (or similar tool) some flexibility in running the job
                if time_since_general_last_cleanup.total_seconds() > 90000:
                    dp.logger.warning(
                        "It's more than 24 hours ago since the general database cleanup was last executed. This may indicate a mistake in your server setup, the general cleanup should always run at least once a day!"
                    )

                if dp.config.cleanup.user_retention_in_days is not None:
                    users_last_cleanup = datetime.fromisoformat(users_last_cleanup_isoformat)
                    time_since_users_last_cleanup = datetime.now() - users_last_cleanup
                    if time_since_users_last_cleanup.total_seconds() > 90000:
                        dp.logger.warning(
                            "It's more than 24 hours ago since the users database cleanup was last executed, even though you enabled user database cleanups in the config. This may indicate a mistake in your server setup, the user cleanup should run at least once a day if enabled!"
                        )
                if dp.config.cleanup.finished_job_retention_in_days is not None:
                    jobs_last_cleanup = datetime.fromisoformat(jobs_last_cleanup_isoformat)
                    time_since_jobs_last_cleanup = datetime.now() - jobs_last_cleanup
                    if time_since_jobs_last_cleanup.total_seconds() > 90000:
                        dp.logger.warning(
                            "It's more than 24 hours ago since the jobs database cleanup was last executed, even though you enabled finished jobs database cleanups in the config. This may indicate a mistake in your server setup, the finished job cleanup should run at least once a day if enabled!"
                        )

    async def _add_local_user_hashed(
        self,
        email: EmailValidated,
        hashed_password: str,
        is_admin: bool = False,
        is_verified: bool = False,
    ) -> int | None:
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=scalar_row) as cur:
                await cur.execute(
                    f"""
                    SELECT id
                    FROM {self.schema}.local_accounts
                    WHERE email = %s
                """,
                    (email.root,),
                )
                if user_id := await cur.fetchone():
                    return None

                await cur.execute(
                    f"""
                    INSERT INTO {self.schema}.users (id)
                    VALUES (DEFAULT)
                    RETURNING id
                    """,
                )
                if user_id := await cur.fetchone():
                    await cur.execute(
                        f"""
                        INSERT INTO {self.schema}.local_accounts (email, id, password_hash, is_admin, is_verified)
                        VALUES (%s, %s, %s, %s, %s)
                    """,
                        (email.root, user_id, hashed_password, is_admin, is_verified),
                    )
                    return user_id
                else:
                    raise Exception(
                        f"Error occurred while creating local user {email}: No user id returned!"
                    )

    async def _ensure_local_user_is_provisioned_hashed(
        self,
        provision_number: int,
        email: EmailValidated,
        hashed_password: str,
        is_admin: bool = False,
    ) -> int:
        async with self.apool.connection() as conn:
            # acquire lock here because multiple workers/pods might try to do this at the same time
            # the if condition below is susceptible to race conditions
            await self.__acquire_advisory_lock(conn)

            async with conn.cursor(row_factory=class_row(LocalUserInternal)) as cur:
                await cur.execute(
                    f"""
                    SELECT *
                    FROM {self.schema}.local_accounts LOCAL, {self.schema}.users USERS
                    WHERE USERS.id = LOCAL.id
                    AND provision_number = %s
                """,
                    (provision_number,),
                )
                if user := await cur.fetchone():
                    await cur.execute(
                        f"""
                            UPDATE {self.schema}.local_accounts
                            SET (email, password_hash, is_admin) = (%s, %s, %s)
                            WHERE id = %s
                        """,
                        (email.root, hashed_password, is_admin, user.id),
                    )
                    return user.id

            async with conn.cursor(row_factory=scalar_row) as cur:
                await cur.execute(
                    f"""
                    INSERT INTO {self.schema}.users (id)
                    VALUES (DEFAULT)
                    RETURNING id
                    """,
                )
                if user_id := await cur.fetchone():
                    await cur.execute(
                        f"""
                        INSERT INTO {self.schema}.local_accounts (email, id, password_hash, is_admin, is_verified, provision_number)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                        (
                            email.root,
                            user_id,
                            hashed_password,
                            is_admin,
                            True,
                            provision_number,
                        ),
                    )
                    return user_id
                else:
                    raise Exception(
                        f"Error occurred while creating local user {email}: No user id returned!"
                    )

    async def ensure_oidc_user_exists(self, iss: str, sub: str, email: EmailValidated) -> int:
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=class_row(OidcUserInternal)) as cur:
                await cur.execute(
                    f"""
                    SELECT *
                    FROM {self.schema}.oidc_accounts OIDC, {self.schema}.users USERS
                    WHERE OIDC.id = USERS.id
                    AND iss = %s AND sub = %s
                """,
                    (iss, sub),
                )
                if user := await cur.fetchone():
                    if user.email.root != email:
                        await cur.execute(
                            f"""
                            UPDATE {self.schema}.oidc_accounts
                            SET email = %s
                            WHERE iss = %s AND sub = %s
                            """,
                            (email.root, iss, sub),
                        )
                    return user.id

            async with conn.cursor(row_factory=scalar_row) as cur:
                await cur.execute(
                    f"""
                    INSERT INTO {self.schema}.users (id)
                    VALUES (DEFAULT)
                    RETURNING id
                    """,
                )
                if user_id := await cur.fetchone():
                    await cur.execute(
                        f"""
                        INSERT INTO {self.schema}.oidc_accounts (iss, sub, id, email)
                        VALUES (%s, %s, %s, %s)
                    """,
                        (iss, sub, user_id, email.root),
                    )
                    return user_id
                else:
                    raise Exception(
                        f"Error occurred while creating oidc user {email}: No user id returned!"
                    )

    async def ensure_ldap_user_exists(
        self, provider_name: str, uid: str, email: EmailValidated
    ) -> int:
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=class_row(LdapUserInternal)) as cur:
                await cur.execute(
                    f"""
                    SELECT *
                    FROM {self.schema}.ldap_accounts LDAP, {self.schema}.users USERS
                    WHERE LDAP.id = USERS.id
                    AND provider_name = %s AND uid = %s
                """,
                    (provider_name, uid),
                )
                if user := await cur.fetchone():
                    if user.email.root != email:
                        await cur.execute(
                            f"""
                            UPDATE {self.schema}.ldap_accounts
                            SET email = %s
                            WHERE provider_name = %s AND uid = %s
                            """,
                            (email.root, provider_name, uid),
                        )
                    return user.id

            async with conn.cursor(row_factory=scalar_row) as cur:
                await cur.execute(
                    f"""
                    INSERT INTO {self.schema}.users (id)
                    VALUES (DEFAULT)
                    RETURNING id
                    """,
                )
                if user_id := await cur.fetchone():
                    await cur.execute(
                        f"""
                        INSERT INTO {self.schema}.ldap_accounts (provider_name, uid, id, email)
                        VALUES (%s, %s, %s, %s)
                    """,
                        (provider_name, uid, user_id, email.root),
                    )
                    return user_id
                else:
                    raise Exception(
                        f"Error occurred while creating ldap user {email}: No user id returned!"
                    )

    async def _add_new_user_token_hashed_encrypted(
        self,
        user_id: int,
        name: str,
        token_hash: str,
        explicit: bool = False,
        admin_privileges: bool = False,
        expires_at: datetime | None = None,
        oidc_refresh_token_id: int | None = None,
        encrypted_oidc_refresh_token: bytes | None = None,
        nonce: str | None = None,
    ):
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=scalar_row) as cur:
                if (
                    oidc_refresh_token_id is None
                    and encrypted_oidc_refresh_token is not None
                    and nonce is not None
                ):
                    await cur.execute(
                        f"""
                        INSERT INTO {self.schema}.oidc_refresh_tokens (encrypted_token, nonce)
                        VALUES (%s, %s)
                        RETURNING id
                        """,
                        (encrypted_oidc_refresh_token, nonce),
                    )
                    oidc_refresh_token_id = await cur.fetchone()
                    if oidc_refresh_token_id is None:
                        raise Exception(
                            "Error occurred while adding a new oidc refresh token to the database"
                        )
                await cur.execute(
                    f"""
                    INSERT INTO {self.schema}.tokens (token_hash, user_id, name, explicit, admin_privileges, expires_at, oidc_refresh_token_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        token_hash,
                        user_id,
                        name,
                        explicit,
                        admin_privileges,
                        expires_at,
                        oidc_refresh_token_id,
                    ),
                )

    async def _rotate_user_token_hashed(
        self, token_id: int, token_hash: str, expires_at: datetime | None = None
    ):
        async with self.apool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    UPDATE {self.schema}.tokens
                    SET (token_hash, expires_at) = (%s, %s)
                    WHERE id = %s
                    """,
                    (token_hash, expires_at, token_id),
                )

    async def delete_user(self, user_id):
        async with self.apool.connection() as conn:
            async with conn.cursor() as cur:
                # jobs of that user get automatically deleted because of the 'ON DELETE CASCADE' specified during table creation
                await cur.execute(
                    f"""
                        DELETE FROM {self.schema}.users
                        WHERE id = %s
                    """,
                    (user_id,),
                )

    async def delete_token_of_user(self, user_id: int, token_id: int):
        async with self.apool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                        DELETE FROM {self.schema}.tokens
                        WHERE id = %s AND user_id = %s
                    """,
                    (token_id, user_id),
                )

    async def delete_all_tokens_of_user(self, user_id: int):
        async with self.apool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                        DELETE FROM {self.schema}.tokens
                        WHERE user_id = %s
                    """,
                    (user_id,),
                )

    async def delete_jobs_of_user(self, user_id: int, job_ids: list[int]):
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=scalar_row) as cur:
                await cur.execute(
                    f"""
                        DELETE FROM {self.schema}.jobs
                        WHERE user_id = %s
                        AND id = ANY(%s)
                        RETURNING job_settings_id
                    """,
                    (user_id, job_ids),
                )
                job_setting_ids = await cur.fetchall()

                # also cleanup job_settings table, but only if no other job references the same job setting anymore and it isn't the default
                await cur.execute(
                    f"""
                        DELETE FROM {self.schema}.job_settings settings
                        WHERE settings.id = ANY(%s)
                        AND settings.is_default = false
                        AND NOT EXISTS (
                            SELECT *
                            FROM {self.schema}.jobs jobs
                            WHERE jobs.job_settings_id = settings.id
                        )
                    """,
                    (job_setting_ids,),
                )

    async def delete_runner(self, runner_id: int):
        async with self.apool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                        DELETE FROM {self.schema}.runners
                        WHERE id = %s
                    """,
                    (runner_id,),
                )

    async def _get_user_by_token_hashed(
        self, token_hash: str
    ) -> tuple[LocalUserInternal | OidcUserInternal | LdapUserInternal, TokenInfoInternal] | None:
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=class_row(TokenInfoInternal)) as cur:
                await cur.execute(
                    f"""
                        UPDATE {self.schema}.tokens
                        SET last_usage = NOW()
                        WHERE token_hash = %s
                        AND (expires_at IS NULL OR expires_at > NOW())
                        RETURNING user_id, id, name, explicit, admin_privileges, expires_at, last_usage, oidc_refresh_token_id
                    """,
                    (token_hash,),
                )
                token = await cur.fetchone()
                if token is None:
                    return None
                user = await self.__get_user_by_id(conn, token.user_id)
                if user is None:
                    return None
                return user, token

    async def __get_user_by_id(
        self, conn: AsyncConnection, user_id: int
    ) -> LocalUserInternal | OidcUserInternal | LdapUserInternal | None:
        # check local_accounts
        async with conn.cursor(row_factory=class_row(LocalUserInternal)) as cur:
            await cur.execute(
                f"""
                SELECT *
                FROM {self.schema}.users USERS, {self.schema}.local_accounts LOCAL
                WHERE USERS.id = LOCAL.id
                AND USERS.id = %s
                """,
                (user_id,),
            )
            if user := await cur.fetchone():
                return user
        # check oidc_accounts
        async with conn.cursor(row_factory=class_row(OidcUserInternal)) as cur:
            await cur.execute(
                f"""
                SELECT *
                FROM {self.schema}.users USERS, {self.schema}.oidc_accounts OIDC
                WHERE USERS.id = OIDC.id
                AND USERS.id = %s
                """,
                (user_id,),
            )
            if user := await cur.fetchone():
                return user
        # check ldap_accounts
        async with conn.cursor(row_factory=class_row(LdapUserInternal)) as cur:
            await cur.execute(
                f"""
                SELECT *
                FROM {self.schema}.users USERS, {self.schema}.ldap_accounts LDAP
                WHERE USERS.id = LDAP.id
                AND USERS.id = %s
                """,
                (user_id,),
            )
            if user := await cur.fetchone():
                return user

            # not found
            return None

    async def get_user_by_id(
        self, user_id: int
    ) -> LocalUserInternal | OidcUserInternal | LdapUserInternal | None:
        async with self.apool.connection() as conn:
            return await self.__get_user_by_id(conn, user_id)

    async def accept_tos_of_user(self, user_id: int, tos_id: int, tos_version: int):
        async with self.apool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    UPDATE {self.schema}.users
                    SET accepted_tos[%s] = to_jsonb(%s)
                    WHERE id = %s
                    """,
                    (tos_id, tos_version, user_id),
                )

    async def get_local_user_by_email(self, email: EmailValidated) -> LocalUserInternal | None:
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=class_row(LocalUserInternal)) as cur:
                await cur.execute(
                    f"""
                    SELECT *
                    FROM {self.schema}.local_accounts la, {self.schema}.users users
                    WHERE la.id = users.id
                    AND la.email = %s
                """,
                    (email.root,),
                )
                return await cur.fetchone()

    async def get_oidc_user_by_iss_sub(self, iss: str, sub: str) -> OidcUserInternal | None:
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=class_row(OidcUserInternal)) as cur:
                await cur.execute(
                    f"""
                        SELECT *
                        FROM {self.schema}.oidc_accounts oa, {self.schema}.users users
                        WHERE oa.id = users.id
                        AND oa.iss = %s AND oa.sub = %s
                    """,
                    (iss, sub),
                )
                return await cur.fetchone()

    async def get_oidc_user_by_id(self, user_id: int) -> OidcUserInternal | None:
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=class_row(OidcUserInternal)) as cur:
                await cur.execute(
                    f"""
                        SELECT *
                        FROM {self.schema}.oidc_accounts oa, {self.schema}.users users
                        WHERE oa.id = users.id
                        AND oa.id = %s
                    """,
                    (user_id,),
                )
                return await cur.fetchone()

    async def get_ldap_user_by_id(self, user_id: int) -> LdapUserInternal | None:
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=class_row(LdapUserInternal)) as cur:
                await cur.execute(
                    f"""
                        SELECT *
                        FROM {self.schema}.ldap_accounts lda, {self.schema}.users users
                        WHERE lda.id = users.id
                        AND lda.id = %s
                    """,
                    (str(user_id),),
                )
                return await cur.fetchone()

    async def get_info_of_all_tokens_of_user(self, user_id: int) -> list[TokenInfoInternal]:
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=class_row(TokenInfoInternal)) as cur:
                await cur.execute(
                    f"""
                        SELECT user_id, id, name, explicit, admin_privileges, expires_at, last_usage, oidc_refresh_token_id
                        FROM {self.schema}.tokens
                        WHERE user_id = %s
                        AND (expires_at IS NULL OR expires_at > NOW())
                        ORDER BY last_usage DESC
                    """,
                    (str(user_id),),
                )
                return await cur.fetchall()

    async def get_all_user_emails(self) -> list[EmailValidated]:
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=scalar_row) as cur:
                await cur.execute(
                    f"""
                        SELECT email
                        FROM {self.schema}.local_accounts
                        UNION
                        SELECT email
                        FROM {self.schema}.oidc_accounts
                        UNION
                        SELECT email
                        FROM {self.schema}.ldap_accounts
                    """
                )
                email_list = await cur.fetchall()
                validated_email_list = []
                for email in email_list:
                    try:
                        validated_email_list.append(EmailValidated.model_validate(email))
                    except ValidationError:
                        dp.logger.error(
                            f"Database contained invalid email address {email}, ignoring..."
                        )
                return validated_email_list

    async def _update_password_hash(self, user_id: int, new_password_hash: str):
        async with self.apool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                        UPDATE {self.schema}.local_accounts
                        SET password_hash = %s
                        WHERE id = %s
                    """,
                    (new_password_hash, user_id),
                )

    async def verify_local_user(self, user_id: int, new_email: EmailValidated):
        async with self.apool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                        UPDATE {self.schema}.local_accounts
                        SET (email, is_verified) = (%s, true)
                        WHERE id = %s
                    """,
                    (new_email.root, user_id),
                )

    async def _update_local_user_password(
        self, email: EmailValidated, hashed_new_password: str
    ) -> int | None:
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=scalar_row) as cur:
                await cur.execute(
                    f"""
                        UPDATE {self.schema}.local_accounts
                        SET password_hash = %s
                        WHERE email = %s
                        RETURNING id
                    """,
                    (hashed_new_password, email.root),
                )
                return await cur.fetchone()

    async def _create_runner_hashed(self, token_hash: str) -> int:
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=scalar_row) as cur:
                await cur.execute(
                    f"""
                        INSERT INTO {self.schema}.runners (token_hash)
                        VALUES (%s)
                        RETURNING id
                    """,
                    (token_hash,),
                )

                if not (runner_id := await cur.fetchone()):
                    raise Exception("Database didn't return runner ID after inserting new runner")
                else:
                    return runner_id

    async def _get_runner_by_token_hashed(self, token_hash: str) -> int | None:
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=class_row(RunnerInternal)) as cur:
                await cur.execute(
                    f"""
                        SELECT *
                        FROM {self.schema}.runners
                        WHERE token_hash = %s
                    """,
                    (token_hash,),
                )

                if not (runner := await cur.fetchone()):
                    return None
                else:
                    return runner.id

    async def add_new_job_settings(
        self, user_id: int, job_settings: JobSettingsResponse, is_new_default: bool = False
    ) -> int:
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=scalar_row) as cur:
                if is_new_default:  # first set the old settings to not be the default anymore
                    await cur.execute(
                        f"""
                            UPDATE {self.schema}.job_settings
                            SET is_default = false
                            WHERE user_id = %s AND is_default = true
                        """,
                        (user_id,),
                    )
                await cur.execute(
                    f"""
                        INSERT INTO {self.schema}.job_settings (user_id, is_default, settings)
                        VALUES (%s, %s, %s)
                        RETURNING id
                    """,
                    (user_id, is_new_default, Jsonb(job_settings.model_dump())),
                )
                if job_settings_id := await cur.fetchone():
                    return job_settings_id
                else:
                    raise Exception("Didn't return id of new job_settings")

    async def get_default_job_settings_of_user(self, user_id: int) -> JobSettingsResponse | None:
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=class_row(JobSettingsResponseWrapped)) as cur:
                await cur.execute(
                    f"""
                        SELECT settings
                        FROM {self.schema}.job_settings
                        WHERE user_id = %s AND is_default = true
                    """,
                    (user_id,),
                )
                if (entry := await cur.fetchone()) is None:
                    return None
                else:
                    return entry.settings

    async def get_job_settings_by_job_id(self, job_id: int) -> JobSettingsResponse | None:
        async with self.apool.connection() as conn:
            # first check if job uses application wide default settings
            async with conn.cursor(row_factory=scalar_row) as cur:
                await cur.execute(
                    f"""
                        SELECT job_settings_id IS NULL
                        FROM {self.schema}.jobs
                        WHERE id = %s
                    """,
                    (job_id,),
                )
                if await cur.fetchone():
                    return JobSettingsRequest()

            async with conn.cursor(row_factory=class_row(JobSettingsResponseWrapped)) as cur:
                await cur.execute(
                    f"""
                        SELECT settings.settings
                        FROM {self.schema}.job_settings settings, {self.schema}.jobs job
                        WHERE job.id = %s AND job.job_settings_id = settings.id
                    """,
                    (job_id,),
                )
                if (entry := await cur.fetchone()) is None:
                    return None
                else:
                    return entry.settings

    async def add_new_job(
        self, user_id: int, audio_file: UploadFile, job_settings_id: int | None = None
    ) -> int | None:
        async with self.apool.connection() as conn:
            if job_settings_id is None:
                async with conn.cursor(row_factory=scalar_row) as cur:
                    await cur.execute(
                        f"""
                                SELECT id
                                FROM {self.schema}.job_settings
                                WHERE user_id = %s AND is_default = true
                            """,
                        (user_id,),
                    )
                    job_settings_id = await cur.fetchone()
            else:
                async with conn.cursor(row_factory=class_row(JobSettingsResponseWrapped)) as cur:
                    await cur.execute(
                        f"""
                            SELECT settings
                            FROM {self.schema}.job_settings
                            WHERE user_id = %s AND id = %s
                        """,
                        (user_id, job_settings_id),
                    )
                    if await cur.fetchone() is None:
                        return None
            async with conn.cursor(row_factory=scalar_row) as cur:
                await cur.execute(
                    """
                        SELECT lo_creat(-1)
                    """
                )
                oid = await cur.fetchone()
                nonce, ciphertext_generator = self._encrypt()
                offset = 0
                while chunk := await audio_file.read(self.__file_chunk_size_in_bytes):
                    await cur.execute(
                        """
                            SELECT lo_put(%s, %s, %s)
                        """,
                        (oid, offset, ciphertext_generator(chunk)),
                    )
                    offset += self.__file_chunk_size_in_bytes
                await cur.execute(
                    f"""
                        INSERT INTO {self.schema}.jobs (user_id, job_settings_id, file_name, audio_oid, nonce)
                        VALUES (%s, %s, %s, %s, %s)
                        RETURNING id
                    """,
                    (user_id, job_settings_id, audio_file.filename, oid, nonce),
                )
                if job_id := await cur.fetchone():
                    return job_id
                else:
                    raise Exception("Didn't return id of new job")

    async def get_total_number_of_jobs_of_user(
        self, user_id: int, excl_finished: bool, excl_downloaded: bool
    ) -> int:
        if excl_finished:
            additional_and = "AND finish_timestamp IS NULL"
        elif excl_downloaded:
            additional_and = "AND (downloaded IS NULL OR downloaded = false)"
        else:
            additional_and = ""
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=scalar_row) as cur:
                await cur.execute(
                    f"""
                        SELECT count(*)
                        FROM {self.schema}.jobs
                        WHERE user_id = %s
                        {additional_and}
                    """,
                    (user_id,),
                )
                if (count := await cur.fetchone()) is None:
                    raise Exception(f"Couldn't get job count of user {user_id}!")
                return count

    async def get_job_ids_of_user(
        self,
        user_id: int,
        start_index: int,
        end_index: int,
        sort_key: JobSortKey,
        desc: bool,
        excl_finished: bool,
        excl_downloaded: bool,
    ) -> list[int]:
        if excl_finished:
            additional_and = "AND finish_timestamp IS NULL"
        elif excl_downloaded:
            additional_and = "AND (downloaded IS NULL OR downloaded = false)"
        else:
            additional_and = ""
        if sort_key == JobSortKey.CREATION_TIME:
            sort_col = "creation_timestamp"
        elif sort_key == JobSortKey.FILENAME:
            sort_col = "file_name"
        if desc:
            comparison_op = (
                "<="  # bigger jobs (left) have less jobs which are larger than it (right)
            )
        else:
            comparison_op = (
                ">="  # bigger jobs (left) have more jobs which are smaller than it (right)
            )
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=scalar_row) as cur:
                await cur.execute(
                    f"""
                    WITH filtered_jobs AS (
                        SELECT *
                        FROM {self.schema}.jobs
                        WHERE user_id = %s
                        {additional_and})
                    SELECT j1.id
                    FROM filtered_jobs j1, filtered_jobs j2
                    WHERE j1.{sort_col} {comparison_op} j2.{sort_col}
                    GROUP BY j1.id
                    HAVING count(*) > %s
                    AND count(*) <= (%s+1)
                    ORDER BY count(*)
                    """,
                    (user_id, start_index, end_index),
                )
                return await cur.fetchall()

    async def get_job_by_id(self, job_id) -> JobInternal | None:
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=class_row(JobInternal)) as cur:
                await cur.execute(
                    f"""
                        SELECT *
                        FROM {self.schema}.jobs
                        WHERE id = %s
                    """,
                    (job_id,),
                )
                return await cur.fetchone()

    async def get_job_infos_of_user(self, user_id: int, job_ids: list[int]) -> list[JobInternal]:
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=class_row(JobInternal)) as cur:
                await cur.execute(
                    f"""
                        SELECT *
                        FROM {self.schema}.jobs
                        WHERE user_id = %s
                        AND id = ANY(%s)
                    """,
                    (user_id, job_ids),
                )
                return await cur.fetchall()

    async def get_job_infos_with_settings_of_user(
        self, user_id: int, job_ids: list[int]
    ) -> list[JobAndSettingsInternal]:
        jobs = []
        async with self.apool.connection() as conn:
            # first query jobs that use application wide default settings
            async with conn.cursor(row_factory=class_row(JobInternal)) as cur:
                await cur.execute(
                    f"""
                        SELECT *
                        FROM {self.schema}.jobs
                        WHERE job_settings_id IS NULL
                        AND id = ANY(%s)
                    """,
                    (job_ids,),
                )
                jobs_without_settings = await cur.fetchall()
                for job in jobs_without_settings:
                    jobs.append(
                        JobAndSettingsInternal.model_validate(
                            {
                                "settings": JobSettingsRequest().model_dump(),
                                **job.model_dump(),
                            }
                        )
                    )
            # then the others
            async with conn.cursor(row_factory=class_row(JobAndSettingsInternal)) as cur:
                await cur.execute(
                    f"""
                        SELECT job.*, settings.settings
                        FROM {self.schema}.job_settings settings, {self.schema}.jobs job
                        WHERE job.job_settings_id = settings.id
                        AND job.user_id = %s
                        AND job.id = ANY(%s)
                    """,
                    (user_id, job_ids),
                )
                jobs += await cur.fetchall()
                return jobs

    async def get_job_audio(self, job_id: int) -> AsyncGenerator[bytes, None]:
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    f"""
                        SELECT audio_oid, nonce
                        FROM {self.schema}.jobs
                        WHERE id = %s
                    """,
                    (job_id,),
                )
                if (
                    (job_details := await cur.fetchone()) is None
                    or (audio_oid := job_details.get("audio_oid")) is None
                    or (nonce := job_details.get("nonce")) is None
                ):
                    return

                plaintext_generator = self._decrypt(nonce)
                offset = 0
            async with conn.cursor(row_factory=scalar_row) as cur:
                await cur.execute(
                    """
                        SELECT lo_get(%s, %s, %s)
                    """,
                    (audio_oid, offset, self.__file_chunk_size_in_bytes),
                )
                while chunk := await cur.fetchone():
                    yield plaintext_generator(chunk)
                    offset += self.__file_chunk_size_in_bytes
                    await cur.execute(
                        """
                            SELECT lo_get(%s, %s, %s)
                        """,
                        (audio_oid, offset, self.__file_chunk_size_in_bytes),
                    )

    async def get_job_transcript_of_user_set_downloaded(
        self, user_id: int, job_id: int, transcript_type: TranscriptTypeEnum
    ) -> str | dict | None:
        async with self.apool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                        SELECT transcript.{transcript_type.value}, {transcript_type.value}_nonce
                        FROM {self.schema}.transcripts transcript, {self.schema}.jobs job
                        WHERE transcript.job_id = job.id
                        AND job.user_id = %s
                        AND transcript.job_id = %s
                    """,
                    (user_id, job_id),
                )
                if (transcript_data := await cur.fetchone()) is not None and len(
                    transcript_data
                ) == 2:
                    await cur.execute(
                        f"""
                            UPDATE {self.schema}.jobs
                            SET downloaded = true
                            WHERE user_id = %s
                            AND id = %s
                        """,
                        (user_id, job_id),
                    )
                    plaintext_generator = self._decrypt(transcript_data[1])
                    return plaintext_generator(transcript_data[0]).decode("utf-8")
                else:
                    return None

    async def get_all_unfinished_jobs(self) -> list[tuple[int, int, bool]]:
        async with self.apool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                        SELECT id, user_id, aborting
                        FROM {self.schema}.jobs
                        WHERE finish_timestamp IS NULL
                    """
                )
                return await cur.fetchall()

    async def get_user_id_of_job(self, job_id: int) -> int | None:
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=scalar_row) as cur:
                await cur.execute(
                    f"""
                        SELECT user_id
                        FROM {self.schema}.jobs
                        WHERE id = %s
                    """,
                    (job_id,),
                )
                return await cur.fetchone()

    async def mark_job_as_aborting(self, job_id: int):
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=scalar_row) as cur:
                await cur.execute(
                    f"""
                        SELECT audio_oid
                        FROM {self.schema}.jobs
                        WHERE id = %s
                    """,
                    (job_id,),
                )
                if (audio_oid := await cur.fetchone()) is None:
                    raise Exception(
                        f"Couldn't get audio_oid for the unfinished job with id {job_id}"
                    )
                await cur.execute(
                    f"""
                        UPDATE {self.schema}.jobs
                        SET (audio_oid, nonce, aborting) = (NULL, NULL, true)
                        WHERE id = %s
                    """,
                    (job_id,),
                )
                await cur.execute(
                    """
                        SELECT lo_unlink(%s)
                    """,
                    (audio_oid,),
                )

    async def finish_successful_job(self, runner: OnlineRunner, transcript: Transcript):
        async with self.apool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                        SELECT audio_oid,aborting
                        FROM {self.schema}.jobs
                        WHERE id = %s
                    """,
                    (runner.assigned_job_id,),
                )
                if (result := await cur.fetchone()) is None:
                    raise Exception(
                        f"Couldn't find unfinished job with id {runner.assigned_job_id} in the database!"
                    )
                (audio_oid, aborting) = result
                if not aborting and audio_oid is None:
                    raise Exception(
                        f"Couldn't get audio_oid for unfinished job with id {runner.assigned_job_id} from the database!"
                    )

                await cur.execute(
                    f"""
                        UPDATE {self.schema}.jobs
                        SET (audio_oid, nonce, finish_timestamp, aborting, downloaded, runner_id, runner_name, runner_version, runner_git_hash, runner_source_code_url) = (NULL, NULL, NOW(), false, false, %s, %s, %s, %s, %s)
                        WHERE id = %s
                    """,
                    (
                        runner.id,
                        runner.name,
                        runner.version,
                        runner.git_hash,
                        runner.source_code_url,
                        runner.assigned_job_id,
                    ),
                )
                txt_nonce, txt_ciphertext_generator = self._encrypt()
                srt_nonce, srt_ciphertext_generator = self._encrypt()
                tsv_nonce, tsv_ciphertext_generator = self._encrypt()
                vtt_nonce, vtt_ciphertext_generator = self._encrypt()
                json_nonce, json_ciphertext_generator = self._encrypt()
                await cur.execute(
                    f"""
                        INSERT INTO {self.schema}.transcripts (job_id, as_txt, as_txt_nonce, as_srt, as_srt_nonce, as_tsv, as_tsv_nonce, as_vtt, as_vtt_nonce, as_json, as_json_nonce)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        runner.assigned_job_id,
                        txt_ciphertext_generator(transcript.as_txt.encode("utf-8")),
                        txt_nonce,
                        srt_ciphertext_generator(transcript.as_srt.encode("utf-8")),
                        srt_nonce,
                        tsv_ciphertext_generator(transcript.as_tsv.encode("utf-8")),
                        tsv_nonce,
                        vtt_ciphertext_generator(transcript.as_vtt.encode("utf-8")),
                        vtt_nonce,
                        json_ciphertext_generator(json.dumps(transcript.as_json).encode("utf-8")),
                        json_nonce,
                    ),
                )
                if audio_oid is not None:
                    await cur.execute(
                        """
                            SELECT lo_unlink(%s)
                        """,
                        (audio_oid,),
                    )

    async def finish_failed_job(
        self, job_id: int, error_msg: str, runner: OnlineRunner | None = None
    ):
        if runner:
            if runner.assigned_job_id != job_id:
                raise Exception("The provided online runner doesn't fit to the provided job_id!")
            runner_id = runner.id
            runner_name = runner.name
            runner_version = runner.version
            runner_git_hash = runner.git_hash
            runner_source_code_url = runner.source_code_url
        else:
            runner_id = None
            runner_name = None
            runner_version = None
            runner_git_hash = None
            runner_source_code_url = None
        async with self.apool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                        SELECT audio_oid,aborting
                        FROM {self.schema}.jobs
                        WHERE id = %s
                    """,
                    (job_id,),
                )
                if (result := await cur.fetchone()) is None:
                    raise Exception(
                        f"Couldn't find unfinished job with id {job_id} in the database!"
                    )
                (audio_oid, aborting) = result
                if not aborting and audio_oid is None:
                    raise Exception(
                        f"Couldn't get audio_oid for unfinished job with id {job_id} from the database!"
                    )

                await cur.execute(
                    f"""
                        UPDATE {self.schema}.jobs
                        SET (audio_oid, nonce, finish_timestamp, aborting, runner_id, runner_name, runner_version, runner_git_hash, runner_source_code_url, error_msg) = (NULL, NULL, NOW(), false, %s, %s, %s, %s, %s, %s)
                        WHERE id = %s
                    """,
                    (
                        runner_id,
                        runner_name,
                        runner_version,
                        runner_git_hash,
                        runner_source_code_url,
                        error_msg,
                        job_id,
                    ),
                )
                if audio_oid is not None:
                    await cur.execute(
                        """
                            SELECT lo_unlink(%s)
                        """,
                        (audio_oid,),
                    )

    async def general_cleanup(self):
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=scalar_row) as cur:
                dp.logger.info("Starting general database cleanup...")
                await cur.execute(
                    f"""
                    SELECT data['general_last_cleanup']
                    FROM {self.schema}.metadata
                    WHERE topic = 'cleanup'
                    """
                )
                last_cleanup_isoformat = await cur.fetchone()
                if last_cleanup_isoformat is None:
                    raise Exception(
                        "Couldn't find general_last_cleanup timestamp in cleanup topic of the metadata table!"
                    )
                last_cleanup = datetime.fromisoformat(last_cleanup_isoformat)
                time_since_last_cleanup = datetime.now() - last_cleanup
                # check for 23h instead of 24h to give cron (or similar tool) some flexibility in running the job
                if time_since_last_cleanup.total_seconds() < 82800:
                    dp.logger.info(
                        "General cleanup was already executed in the last 24 hours. Not executing again, skipped cleanup"
                    )
                    return

                dp.logger.info("Cleaning up orphaned large objects...")
                await cur.execute(
                    f"""
                    SELECT lo_unlink(lo.oid)
                    FROM pg_largeobject_metadata lo, pg_roles roles
                    WHERE roles.rolname = current_user
                    AND lo.lomowner = roles.oid
                    AND NOT EXISTS (
                        SELECT 1
                        FROM {self.schema}.jobs
                        WHERE audio_oid = lo.oid
                    )
                    """
                )

                dp.logger.info("Cleaning up orphaned job settings rows...")
                await cur.execute(
                    f"""
                    DELETE FROM {self.schema}.job_settings job_settings
                    WHERE NOT is_default
                    AND NOT EXISTS (
                        SELECT 1
                        FROM {self.schema}.jobs
                        WHERE job_settings_id = job_settings.id
                    )
                    """
                )

                dp.logger.info("Cleaning up expired auth tokens...")
                # don't delete most recently used token so that it can still be used for user cleanup
                await cur.execute(
                    f"""
                    DELETE
                    FROM {self.schema}.tokens token
                    WHERE expires_at IS NOT NULL
                    AND expires_at < NOW()
                    AND last_usage NOT IN (
                        SELECT max(last_usage)
                        FROM {self.schema}.tokens
                        WHERE user_id = token.user_id
                    )
                    """
                )
                await cur.execute(
                    f"""
                    DELETE
                    FROM {self.schema}.oidc_refresh_tokens oidc_token
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM {self.schema}.tokens token
                        WHERE token.oidc_refresh_token_id = oidc_token.id
                    )
                    """
                )

                await cur.execute(
                    f"""
                    UPDATE {self.schema}.metadata
                    SET data['general_last_cleanup'] = %s
                    WHERE topic = 'cleanup'
                """,
                    (Jsonb(datetime.now().isoformat()),),
                )
        dp.logger.info("General database cleanup complete")

    async def user_cleanup(self, retention_time_in_days: int):
        assert dp.config.cleanup.user_retention_in_days is not None
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=scalar_row) as cur:
                dp.logger.info("Starting user database cleanup...")
                await cur.execute(
                    f"""
                    SELECT data['users_last_cleanup']
                    FROM {self.schema}.metadata
                    WHERE topic = 'cleanup'
                    """
                )
                last_cleanup_isoformat = await cur.fetchone()
                if last_cleanup_isoformat is None:
                    raise Exception(
                        "Couldn't find users_last_cleanup timestamp in cleanup topic of the metadata table!"
                    )
                last_cleanup = datetime.fromisoformat(last_cleanup_isoformat)
                time_since_last_cleanup = datetime.now() - last_cleanup
                if time_since_last_cleanup.total_seconds() < 82800:
                    dp.logger.info(
                        "User cleanup was already executed in the last 24 hours. Not executing again, skipped cleanup"
                    )
                    return

                dp.logger.info(
                    f"Cleaning up users that haven't logged in {retention_time_in_days} days..."
                )
                # first send emails out to users who's accounts will be deleted in 30 days/7 days
                await cur.execute(
                    f"""
                    SELECT la.email
                    FROM {self.schema}.users users, {self.schema}.local_accounts la, {self.schema}.tokens tokens
                    WHERE users.id = la.id
                    AND tokens.user_id = users.id
                    AND tokens.last_usage = (
                        SELECT max(last_usage)
                        FROM {self.schema}.tokens
                        WHERE user_id = users.id
                    )
                    AND NOW() - tokens.last_usage BETWEEN %(interval_begin)s AND %(interval_end)s
                    AND la.provision_number IS NULL
                    UNION
                    SELECT oa.email
                    FROM {self.schema}.users users, {self.schema}.oidc_accounts oa, {self.schema}.tokens tokens
                    WHERE users.id = oa.id
                    AND tokens.user_id = users.id
                    AND tokens.last_usage = (
                        SELECT max(last_usage)
                        FROM {self.schema}.tokens
                        WHERE user_id = users.id
                    )
                    AND NOW() - tokens.last_usage BETWEEN %(interval_begin)s AND %(interval_end)s
                    UNION
                    SELECT lda.email
                    FROM {self.schema}.users users, {self.schema}.ldap_accounts lda, {self.schema}.tokens tokens
                    WHERE users.id = lda.id
                    AND tokens.user_id = users.id
                    AND tokens.last_usage = (
                        SELECT max(last_usage)
                        FROM {self.schema}.tokens
                        WHERE user_id = users.id
                    )
                    AND NOW() - tokens.last_usage BETWEEN %(interval_begin)s AND %(interval_end)s
                    """,
                    {
                        "interval_begin": timedelta(days=retention_time_in_days - 31),
                        "interval_end": timedelta(days=retention_time_in_days - 30),
                    },
                )
                users_30_days_notif = await cur.fetchall()
                await cur.execute(
                    f"""
                    SELECT la.email
                    FROM {self.schema}.users users, {self.schema}.local_accounts la, {self.schema}.tokens
                    WHERE users.id = la.id
                    AND tokens.user_id = users.id
                    AND tokens.last_usage = (
                        SELECT max(last_usage)
                        FROM {self.schema}.tokens
                        WHERE user_id = users.id
                    )
                    AND NOW() - tokens.last_usage BETWEEN %(interval_begin)s AND %(interval_end)s
                    AND la.provision_number IS NULL
                    UNION
                    SELECT oa.email
                    FROM {self.schema}.users users, {self.schema}.oidc_accounts oa, {self.schema}.tokens
                    WHERE users.id = oa.id
                    AND tokens.user_id = users.id
                    AND tokens.last_usage = (
                        SELECT max(last_usage)
                        FROM {self.schema}.tokens
                        WHERE user_id = users.id
                    )
                    AND NOW() - tokens.last_usage BETWEEN %(interval_begin)s AND %(interval_end)s
                    UNION
                    SELECT lda.email
                    FROM {self.schema}.users users, {self.schema}.ldap_accounts lda, {self.schema}.tokens
                    WHERE users.id = lda.id
                    AND tokens.user_id = users.id
                    AND tokens.last_usage = (
                        SELECT max(last_usage)
                        FROM {self.schema}.tokens
                        WHERE user_id = users.id
                    )
                    AND NOW() - tokens.last_usage BETWEEN %(interval_begin)s AND %(interval_end)s
                    """,
                    {
                        "interval_begin": timedelta(days=retention_time_in_days - 8),
                        "interval_end": timedelta(days=retention_time_in_days - 7),
                    },
                )
                users_7_days_notif = await cur.fetchall()
                users_30_days_notif_validated = []
                for email in users_30_days_notif:
                    try:
                        users_30_days_notif_validated.append(EmailValidated.model_validate(email))
                    except ValidationError:
                        dp.logger.error(
                            f"Database contained invalid email address {email}, ignoring..."
                        )
                if len(users_30_days_notif_validated) > 0:
                    await dp.smtp.send_account_deletion_reminder(
                        users_30_days_notif_validated, dp.config.client_url, 30
                    )
                    dp.logger.info(
                        f"Sent 30 day account deletion reminder to {len(users_30_days_notif_validated)} users"
                    )
                else:
                    dp.logger.info("Didn't have to send any 30 day account deletion reminders")
                users_7_days_notif_validated = []
                for email in users_7_days_notif:
                    try:
                        users_7_days_notif_validated.append(EmailValidated.model_validate(email))
                    except ValidationError:
                        dp.logger.error(
                            f"Database contained invalid email address {email}, ignoring..."
                        )
                if len(users_7_days_notif_validated) > 0:
                    await dp.smtp.send_account_deletion_reminder(
                        users_7_days_notif_validated, dp.config.client_url, 7
                    )
                    dp.logger.info(
                        f"Sent 7 day account deletion reminder to {len(users_7_days_notif_validated)} users"
                    )
                else:
                    dp.logger.info("Didn't have to send any 7 day account deletion reminders")

                await cur.execute(
                    f"""
                    DELETE
                    FROM {self.schema}.users users
                    WHERE EXISTS (
                        SELECT 1
                        FROM {self.schema}.tokens
                        WHERE user_id = users.id
                        AND tokens.last_usage = (
                            SELECT max(last_usage)
                            FROM {self.schema}.tokens
                            WHERE user_id = users.id
                        )
                        AND tokens.last_usage < NOW() - %s
                    )
                    AND NOT EXISTS (
                        SELECT 1
                        FROM {self.schema}.local_accounts la
                        WHERE la.id = users.id
                        AND la.provision_number IS NOT NULL
                    )
                    """,
                    (timedelta(days=retention_time_in_days),),
                )
                await cur.execute(
                    f"""
                    UPDATE {self.schema}.metadata
                    SET data['users_last_cleanup'] = %s
                    WHERE topic = 'cleanup'
                """,
                    (Jsonb(datetime.now().isoformat()),),
                )
        dp.logger.info("User database cleanup complete")

    async def job_cleanup(self, retention_time_in_days: int):
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=scalar_row) as cur:
                dp.logger.info("Starting job database cleanup...")
                await cur.execute(
                    f"""
                    SELECT data['jobs_last_cleanup']
                    FROM {self.schema}.metadata
                    WHERE topic = 'cleanup'
                    """
                )
                last_cleanup_isoformat = await cur.fetchone()
                if last_cleanup_isoformat is None:
                    raise Exception(
                        "Couldn't find jobs_last_cleanup timestamp in cleanup topic of the metadata table!"
                    )
                last_cleanup = datetime.fromisoformat(last_cleanup_isoformat)
                time_since_last_cleanup = datetime.now() - last_cleanup
                if time_since_last_cleanup.total_seconds() < 82800:
                    dp.logger.info(
                        "Job cleanup was already executed in the last 24 hours. Not executing again, skipped cleanup"
                    )
                    return

                dp.logger.info(
                    f"Cleaning up jobs that have finished more than {retention_time_in_days} days ago..."
                )
                await cur.execute(
                    f"""
                    DELETE
                    FROM {self.schema}.jobs
                    WHERE finish_timestamp IS NOT NULL
                    AND finish_timestamp < NOW() - %s
                    """,
                    (timedelta(days=retention_time_in_days),),
                )
                await cur.execute(
                    f"""
                    UPDATE {self.schema}.metadata
                    SET data['jobs_last_cleanup'] = %s
                    WHERE topic = 'cleanup'
                """,
                    (Jsonb(datetime.now().isoformat()),),
                )
        dp.logger.info("Job database cleanup complete")

    async def get_ldap_tokens(self) -> list[LdapTokenInfoInternal]:
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=class_row(LdapTokenInfoInternal)) as cur:
                await cur.execute(
                    f"""
                    SELECT token.user_id, token.id, token.name, token.explicit, token.admin_privileges, token.expires_at, token.last_usage, token.oidc_refresh_token_id, ldap.provider_name, ldap.uid
                    FROM {self.schema}.tokens token, {self.schema}.ldap_accounts ldap
                    WHERE token.user_id = ldap.id
                    """
                )
                return await cur.fetchall()

    async def get_oidc_tokens(self) -> list[OidcTokenInfoInternal]:
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=class_row(OidcTokenInfoInternal)) as cur:
                await cur.execute(
                    f"""
                    SELECT token.user_id, token.id, token.name, token.explicit, token.admin_privileges, token.expires_at, token.last_usage, token.oidc_refresh_token_id, oidc.iss, oidc.sub
                    FROM {self.schema}.tokens token, {self.schema}.oidc_accounts oidc
                    WHERE token.user_id = oidc.id
                    """
                )
                return await cur.fetchall()

    async def get_oidc_refresh_token_of_token(self, token_id: int) -> SecretStr | None:
        async with self.apool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT oidc_token.encrypted_token, oidc_token.nonce
                    FROM {self.schema}.oidc_refresh_tokens oidc_token, {self.schema}.tokens token
                    WHERE token.id = %s
                    AND oidc_token.id = token.oidc_refresh_token_id
                    """,
                    (token_id,),
                )
                if (return_tuple := await cur.fetchone()) is not None and len(return_tuple) == 2:
                    plaintext_generator = self._decrypt(return_tuple[1])
                    return SecretStr(plaintext_generator(return_tuple[0]).decode("utf-8"))
                return None

    async def replace_oidc_refresh_token(
        self, oidc_refresh_token_id: int, new_refresh_token: SecretStr
    ):
        nonce, ciphertext_generator = self._encrypt()
        new_refresh_token_encrypted = ciphertext_generator(
            new_refresh_token.get_secret_value().encode("utf-8")
        )
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=scalar_row) as cur:
                await cur.execute(
                    f"""
                    UPDATE {self.schema}.oidc_refresh_tokens
                    SET (encrypted_token, nonce) = (%s, %s)
                    WHERE id = %s
                    """,
                    (new_refresh_token_encrypted, nonce, oidc_refresh_token_id),
                )

    async def add_site_banner(self, urgency: int, html: str) -> int | None:
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=scalar_row) as cur:
                await cur.execute(
                    f"""
                    INSERT INTO {self.schema}.site_data (type, urgency, html)
                    VALUES ('banner', %s, %s)
                    RETURNING id
                """,
                    (urgency, html),
                )
                return await cur.fetchone()

    async def list_site_banners(self) -> list[SiteBannerResponse]:
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=class_row(SiteBannerResponse)) as cur:
                await cur.execute(
                    f"""
                    SELECT id,urgency,html
                    FROM {self.schema}.site_data
                    WHERE type = 'banner'
                    ORDER BY urgency DESC
                    """
                )
                return await cur.fetchall()

    async def delete_site_banner(self, id: int):
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=class_row(SiteBannerResponse)) as cur:
                await cur.execute(
                    f"""
                    DELETE
                    FROM {self.schema}.site_data
                    WHERE type = 'banner'
                    AND id = %s
                    """,
                    (id,),
                )

    async def rotate_secret_key(self, new_secret_key: SecretKeyValidated):
        async with self.apool.connection() as conn:
            # re-encrypt audio files
            async with conn.cursor(row_factory=class_row(JobInternal)) as cur:
                await cur.execute(
                    f"""
                    SELECT *
                    FROM {self.schema}.jobs
                    WHERE audio_oid IS NOT NULL
                    """
                )
                audio_files = await cur.fetchall()
            async with conn.cursor(row_factory=scalar_row) as cur:
                for file in audio_files:
                    if file.nonce is None or file.audio_oid is None:
                        raise Exception("Invalid job in database!")
                    plaintext_generator = self._decrypt(file.nonce)
                    new_nonce, ciphertext_generator = self._encrypt(new_secret_key)
                    await cur.execute(
                        """
                            SELECT lo_creat(-1)
                        """
                    )
                    new_oid = await cur.fetchone()
                    offset = 0
                    await cur.execute(
                        """
                            SELECT lo_get(%s, %s, %s)
                        """,
                        (file.audio_oid, offset, self.__file_chunk_size_in_bytes),
                    )
                    while chunk := await cur.fetchone():
                        decrypted_chunk = plaintext_generator(chunk)
                        await cur.execute(
                            """
                                SELECT lo_put(%s, %s, %s)
                            """,
                            (new_oid, offset, ciphertext_generator(decrypted_chunk)),
                        )
                        offset += self.__file_chunk_size_in_bytes
                        await cur.execute(
                            """
                                SELECT lo_get(%s, %s, %s)
                            """,
                            (file.audio_oid, offset, self.__file_chunk_size_in_bytes),
                        )
                    await cur.execute(
                        f"""
                            UPDATE {self.schema}.jobs
                            SET (audio_oid, nonce) = (%s, %s)
                            WHERE id = %s
                        """,
                        (new_oid, new_nonce, file.id),
                    )

            # re-encrypt transcripts
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    f"""
                    SELECT *
                    FROM {self.schema}.transcripts
                    """
                )
                transcripts = await cur.fetchall()
                for transcript in transcripts:
                    txt_plaintext_generator = self._decrypt(transcript["as_txt_nonce"])
                    new_txt_nonce, txt_ciphertext_generator = self._encrypt(new_secret_key)
                    srt_plaintext_generator = self._decrypt(transcript["as_srt_nonce"])
                    new_srt_nonce, srt_ciphertext_generator = self._encrypt(new_secret_key)
                    tsv_plaintext_generator = self._decrypt(transcript["as_tsv_nonce"])
                    new_tsv_nonce, tsv_ciphertext_generator = self._encrypt(new_secret_key)
                    vtt_plaintext_generator = self._decrypt(transcript["as_vtt_nonce"])
                    new_vtt_nonce, vtt_ciphertext_generator = self._encrypt(new_secret_key)
                    json_plaintext_generator = self._decrypt(transcript["as_json_nonce"])
                    new_json_nonce, json_ciphertext_generator = self._encrypt(new_secret_key)
                    await cur.execute(
                        f"""
                        UPDATE {self.schema}.transcripts
                        SET (as_txt, as_txt_nonce, as_srt, as_srt_nonce, as_tsv, as_tsv_nonce, as_vtt, as_vtt_nonce, as_json, as_json_nonce) = (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        WHERE job_id = %s
                    """,
                        (
                            txt_ciphertext_generator(txt_plaintext_generator(transcript["as_txt"])),
                            new_txt_nonce,
                            srt_ciphertext_generator(srt_plaintext_generator(transcript["as_srt"])),
                            new_srt_nonce,
                            tsv_ciphertext_generator(tsv_plaintext_generator(transcript["as_tsv"])),
                            new_tsv_nonce,
                            vtt_ciphertext_generator(vtt_plaintext_generator(transcript["as_vtt"])),
                            new_vtt_nonce,
                            json_ciphertext_generator(
                                json_plaintext_generator(transcript["as_json"])
                            ),
                            new_json_nonce,
                            transcript["job_id"],
                        ),
                    )

                # re-encrypt oidc refresh tokens
                await cur.execute(
                    f"""
                    SELECT *
                    FROM {self.schema}.oidc_refresh_tokens
                    """
                )
                oidc_tokens = await cur.fetchall()
                for token in oidc_tokens:
                    plaintext_generator = self._decrypt(token["nonce"])
                    new_nonce, ciphertext_generator = self._encrypt(new_secret_key)
                    await cur.execute(
                        f"""
                        UPDATE {self.schema}.oidc_refresh_tokens
                        SET (encrypted_token, nonce) = (%s, %s)
                        WHERE id = %s
                    """,
                        (
                            ciphertext_generator(plaintext_generator(token["encrypted_token"])),
                            new_nonce,
                            token["id"],
                        ),
                    )

    async def delete_encrypted_content(self):
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=scalar_row) as cur:
                await cur.execute(  # also deletes transcripts
                    f"""
                    DELETE
                    FROM {self.schema}.jobs
                    """
                )
                await cur.execute(
                    f"""
                    DELETE
                    FROM {self.schema}.oidc_refresh_tokens
                    """
                )
