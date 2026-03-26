from abc import ABC, abstractmethod
from typing import AsyncGenerator

import redis.asyncio as redis
from pydantic import ValidationError
from redis.asyncio.client import Pipeline
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from project_W_lib.models.base import Token
from project_W_lib.models.request_models import RunnerRegisterRequest

import project_W.dependencies as dp

from .models.internal_models import InProcessJob, OnlineRunner, SSEEvent
from .models.setting_models import RedisConnection


class CachingAdapter(ABC):
    @abstractmethod
    async def open(self, connection_obj):
        """
        This method initiates the connection pool and is called only on application startup.
        """
        pass

    @abstractmethod
    async def close(self):
        """
        This method teares down the connections and is called only on application shutdown
        """
        pass

    @abstractmethod
    async def register_new_online_runner(
        self, runner_id: int, runner_data: RunnerRegisterRequest
    ) -> Token:
        """
        This method registers a runner as online
        Returns the new runner session token
        """
        pass

    @abstractmethod
    async def reset_runner_expiration(self, runner_id: int):
        """
        Resets the expiration time (TTL) of the runner with runner_id to its initial value if it exists if it exists
        """
        pass

    @abstractmethod
    async def mark_job_of_runner_in_progress(self, runner_id: int):
        """
        Marks an assigned job of the runner with id runner_id as in progress
        """
        pass

    @abstractmethod
    async def get_online_runner_by_id(self, runner_id: int) -> OnlineRunner | None:
        """
        Get all attributes associated with an online runner by querying it using its id
        Return None if no online runner exists with that id
        """
        pass

    @abstractmethod
    async def unassign_current_job_from_online_runner(self, runner: OnlineRunner):
        """
        Unassign a job from an online runner and add the runner back to the runner queue making it available to be assigned to new jobs.
        This is called when the runner submits the job before the backend processed the job result
        """
        pass

    @abstractmethod
    async def finish_job_of_online_runner(self, job_id: int):
        """
        Removes the job from processing jobs. Call unassign_current_job_from_online_runner In conjunction with this
        """
        pass

    @abstractmethod
    async def unregister_online_runner(self, runner_id: int):
        """
        Unregister an existing online runner (i.e. remove it from caching, it went offline)
        """
        pass

    @abstractmethod
    async def get_online_runner_id_by_assigned_job(self, job_id: int) -> int | None:
        """
        Returns the runner id of the runner that the job with the id job_id is currently assigned to
        Returns None if not found
        """
        pass

    @abstractmethod
    async def enqueue_new_job(self, job_id: int, job_priority: int):
        """
        Push a new job into the job queue
        """
        pass

    @abstractmethod
    async def remove_job_from_queue(self, job_id: int):
        """
        Removes the job with the id job_id from the queue if it exists, regardless its position
        """
        pass

    @abstractmethod
    async def assign_job_to_runner_if_possible(self, job_id: int, user_id: int):
        """
        Assigns the provided job to a free runner (if there is one)
        Does nothing if not
        """
        pass

    @abstractmethod
    async def assign_queue_job_to_runner_if_possible(self):
        """
        Assigns an unassigned job from the queue to a free runner (if both exist)
        Does nothing if not
        """
        pass

    @abstractmethod
    async def get_in_process_job(self, job_id: int) -> InProcessJob | None:
        """
        Returns InProcessJob attributes for job_id, or None if there are no in process jobs with that id
        """
        pass

    @abstractmethod
    async def abort_in_process_job(self, job_id: int):
        """
        Mark an in process job for abortion
        """
        pass

    @abstractmethod
    async def report_progress_of_in_process_job(self, job_id: int, progress: float):
        """
        Sets the progress value of a job
        """
        pass

    @abstractmethod
    async def queue_contains_job(self, job_id: int) -> bool:
        """
        Returns whether the queue contains a job
        """
        pass

    @abstractmethod
    async def send_event(self, user_id: int, event: SSEEvent, data: str):
        """
        Sends an SSE event to event_generator
        """
        pass

    @abstractmethod
    async def event_generator(self, user_id: int) -> AsyncGenerator[str, None]:
        """
        This is a Generator method that returns events regarding jobs
         of user with id user_id in a SSE format
        """
        yield ""


class RedisAdapter(CachingAdapter):
    minimal_required_redis_version = [7, 2]

    __heartbeat_timeout = 60

    __runner_sorted_set_name = "online_runners_sorted"

    __job_queue_sorted_set_name = "job_queue_sorted"

    def __get_runner_key(self, runner_id: int) -> str:
        return f"online_runner:{str(runner_id)}"

    def __get_job_key(self, job_id: int) -> str:
        return f"in_process_job:{str(job_id)}"

    def __get_pubsub_channel(self, event: SSEEvent, user_id: int) -> str:
        return f"{event.value}:{str(user_id)}"

    async def open(self, connection_obj: RedisConnection):
        # 3 retries on timeout
        retry = Retry(ExponentialBackoff(), 3)
        redis_extra_args = {
            "retry": retry,
            "retry_on_timeout": True,
            "decode_responses": True,
        }
        self.client = redis.StrictRedis(
            unix_socket_path=str(connection_obj.unix_socket_path), **redis_extra_args
        )  # implicitly creates connection pool
        if connection_obj.connection_string is not None:
            self.client = self.client.from_url(
                str(connection_obj.connection_string), **redis_extra_args
            )

        if not await self.client.ping():
            raise Exception(
                "Critical: Redis doesn't answer to pings. Make sure the redis server is up and running and the connection settings are correct!"
            )

        await self.__check_server_version()

        dp.logger.info("Successfully connected to Redis")

    async def __check_server_version(self):
        query_result = await self.client.info("server")
        if query_result is None:
            raise Exception("Could not check Redis server version")
        redis_version = query_result["redis_version"]
        redis_version_split = list(map(int, str(redis_version).split(".")))
        for i in range(min(len(self.minimal_required_redis_version), len(redis_version_split))):
            if redis_version_split[i] < self.minimal_required_redis_version[i]:
                raise Exception(
                    f"The version of the specified Redis instance is {redis_version} while the minimal required version is {'.'.join(map(str, self.minimal_required_redis_version))}"
                )
            elif redis_version_split[i] > self.minimal_required_redis_version[i]:
                break

        dp.logger.info(f"Redis server is on version {redis_version}")

    async def close(self):
        dp.logger.info("Closing Redis connections...")
        await self.client.close()

    async def register_new_online_runner(
        self, runner_id: int, runner_data: RunnerRegisterRequest
    ) -> Token:
        token = Token()
        async with self.client.pipeline(transaction=True) as pipe:
            key_name = self.__get_runner_key(runner_id)
            runner_dump = runner_data.model_dump(exclude_none=True)
            runner_dump["in_process"] = 0
            runner_dump["session_token_hash"] = token.hash()
            pipe.hset(key_name, mapping=runner_dump)
            pipe.expire(key_name, self.__heartbeat_timeout)
            pipe.zadd(
                self.__runner_sorted_set_name,
                {str(runner_id): runner_data.priority},
            )
            await pipe.execute()
        return token

    async def __reset_runner_expiration(self, pipe: Pipeline, runner_id: int):
        pipe.hget(self.__get_runner_key(runner_id), "assigned_job_id")
        (job_id,) = await pipe.execute()
        if job_id is not None:
            pipe.expire(self.__get_job_key(job_id), self.__heartbeat_timeout)
        pipe.expire(self.__get_runner_key(runner_id), self.__heartbeat_timeout)
        await pipe.execute()

    async def reset_runner_expiration(self, runner_id: int):
        async with self.client.pipeline(transaction=True) as pipe:
            return await self.__reset_runner_expiration(pipe, runner_id)

    async def mark_job_of_runner_in_progress(self, runner_id: int):
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.hset(self.__get_runner_key(runner_id), "in_process", "1")
            await pipe.execute()

    async def get_online_runner_by_id(self, runner_id: int) -> OnlineRunner | None:
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.hgetall(self.__get_runner_key(runner_id))
            (runner_dict,) = await pipe.execute()
            if not runner_dict:
                return None
            runner_dict["id"] = runner_id
            try:
                return OnlineRunner.model_validate(runner_dict)
            except ValidationError:
                dp.logger.error(
                    f"Validation error occurred while reading from redis! The following data was read instead of an OnlineRunner object: {runner_dict}. Continuing..."
                )
                return None

    async def unassign_current_job_from_online_runner(self, runner: OnlineRunner):
        assert runner.assigned_job_id is not None
        assert runner.in_process is True
        async with self.client.pipeline(transaction=True) as pipe:
            # to give the backend some time to process the submitted transcript, reset it's expiration here
            await self.__reset_runner_expiration(pipe, runner.id)
            pipe.hdel(self.__get_runner_key(runner.id), *["in_process", "assigned_job_id"])
            pipe.zadd(
                self.__runner_sorted_set_name,
                {str(runner.id): runner.priority},
            )
            await pipe.execute()

    async def finish_job_of_online_runner(self, job_id: int):
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.hget(self.__get_job_key(job_id), "user_id")
            (user_id,) = await pipe.execute()
            pipe.delete(self.__get_job_key(job_id))
            pipe.zrem(self.__job_queue_sorted_set_name, job_id)
            if user_id is not None:
                self.__send_event(pipe, user_id, SSEEvent.JOB_UPDATED, str(job_id))
            await pipe.execute()
            if user_id is None:
                raise Exception(f"Redis didn't return a user_id for the job with id {job_id}!")

    async def unregister_online_runner(self, runner_id: int):
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.hget(self.__get_runner_key(runner_id), "assigned_job_id")
            pipe.delete(self.__get_runner_key(runner_id))
            pipe.zrem("online_runners_sorted", runner_id)
            (job_id, _, _) = await pipe.execute()
            if job_id is not None:
                job_id = int(job_id)
                pipe.hget(self.__get_job_key(job_id), "user_id")
                pipe.hget(self.__get_job_key(job_id), "abort")
                (user_id, abort) = await pipe.execute()
                pipe.delete(self.__get_job_key(job_id))
                if bool(int(abort)):
                    pipe.zrem(self.__job_queue_sorted_set_name, job_id)
                    await pipe.execute()
                    await dp.db.finish_failed_job(job_id, "Job was aborted")
                if user_id is not None:
                    self.__send_event(pipe, user_id, SSEEvent.JOB_UPDATED, str(job_id))
                await pipe.execute()
                if user_id is None:
                    raise Exception(f"Redis didn't return a user_id for the job with id {job_id}!")
                await self.__assign_job_to_runner_if_possible(pipe, job_id, user_id)

    async def get_online_runner_id_by_assigned_job(self, job_id: int) -> int | None:
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.hget(self.__get_job_key(job_id), "runner_id")
            (runner_id,) = await pipe.execute()
            if not runner_id:
                return None
            return int(runner_id)

    async def enqueue_new_job(self, job_id: int, job_priority: int):
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.zadd(self.__job_queue_sorted_set_name, {str(job_id): job_priority})
            await pipe.execute()

    async def remove_job_from_queue(self, job_id: int):
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.zrem(self.__job_queue_sorted_set_name, job_id)
            await pipe.execute()

    async def __assign_job_to_runner_if_possible(self, pipe: Pipeline, job_id: int, user_id: int):
        # get runner to assign this to
        pipe.zpopmax(self.__runner_sorted_set_name)
        (ids_returned,) = await pipe.execute()
        if len(ids_returned) == 0:
            return
        else:
            runner_id = ids_returned[0][0]
        key_name = self.__get_runner_key(runner_id)
        pipe.hgetall(key_name)
        (runner_dict,) = await pipe.execute()
        while (not runner_dict) or (runner_dict.get("assigned_job_id") is not None):
            pipe.zpopmax(self.__runner_sorted_set_name)
            (ids_returned,) = await pipe.execute()
            if len(ids_returned) == 0:
                return
            else:
                runner_id = ids_returned[0][0]
            key_name = self.__get_runner_key(runner_id)
            pipe.hgetall(key_name)
            (runner_dict,) = await pipe.execute()

        online_runner = OnlineRunner.model_validate(runner_dict | {"id": int(runner_id)})
        online_runner.assigned_job_id = job_id
        runner_dump = online_runner.model_dump(exclude_none=True)
        runner_dump["in_process"] = int(runner_dump["in_process"])
        pipe.hset(key_name, mapping=runner_dump)
        pipe.hset(
            self.__get_job_key(job_id),
            mapping={
                "runner_id": runner_id,
                "user_id": user_id,
                "progress": 0.0,
                "abort": 0,
            },
        )
        self.__send_event(pipe, user_id, SSEEvent.JOB_UPDATED, str(job_id))
        await pipe.execute()
        await self.__reset_runner_expiration(
            pipe, runner_id
        )  # to sync expiration between runner and job hash

    async def assign_job_to_runner_if_possible(self, job_id: int, user_id: int):
        async with self.client.pipeline(transaction=True) as pipe:
            return await self.__assign_job_to_runner_if_possible(pipe, job_id, user_id)

    async def assign_queue_job_to_runner_if_possible(self):
        # TODO: If we have runner tags, only assign job if it has the right tag.
        async with self.client.pipeline(transaction=True) as pipe:
            i = 0
            pipe.zrevrange(self.__job_queue_sorted_set_name, i, i)
            (ids_returned,) = await pipe.execute()
            if len(ids_returned) == 0:
                return
            else:
                job_id = int(ids_returned[0])
            pipe.exists(self.__get_job_key(job_id))
            (skip_job,) = await pipe.execute()
            if not skip_job:
                job = await dp.db.get_job_by_id(job_id)
                if job is None:
                    raise Exception(
                        f"Redis/Postgresql data mismatch: Read a job with id {job_id} from redis that doesn't exist in Postgresql!"
                    )
                if job.aborting:
                    pipe.zrem(self.__job_queue_sorted_set_name, job_id)
                    await pipe.execute()
                    await dp.db.finish_failed_job(job_id, "Job was aborted")
                    self.__send_event(pipe, job.user_id, SSEEvent.JOB_UPDATED, str(job_id))
                    await pipe.execute()
                    skip_job = True
            while skip_job:
                i += 1
                pipe.zrevrange(self.__job_queue_sorted_set_name, i, i)
                values = await pipe.execute()
                (ids_returned,) = values
                if len(ids_returned) == 0:
                    return
                else:
                    job_id = int(ids_returned[0])
                pipe.exists(self.__get_job_key(job_id))
                (skip_job,) = await pipe.execute()
                if not skip_job:
                    job = await dp.db.get_job_by_id(job_id)
                    if job is None:
                        raise Exception(
                            f"Redis/Postgresql data mismatch: Read a job with id {job_id} from redis that doesn't exist in Postgresql!"
                        )
                    if job.aborting:
                        pipe.zrem(self.__job_queue_sorted_set_name, job_id)
                        await pipe.execute()
                        await dp.db.finish_failed_job(job_id, "Job was aborted")
                        self.__send_event(pipe, job.user_id, SSEEvent.JOB_UPDATED, str(job_id))
                        await pipe.execute()
                        skip_job = True

            # query user id from database for sending event
            user_id = await dp.db.get_user_id_of_job(job_id)
            if user_id is None:
                raise Exception(
                    f"Redis/Postgresql data mismatch: Read a job with id {job_id} from redis that doesn't exist in Postgresql!"
                )

            await self.__assign_job_to_runner_if_possible(pipe, job_id, user_id)

    async def get_in_process_job(self, job_id: int) -> InProcessJob | None:
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.hgetall(self.__get_job_key(job_id))
            (job_dict,) = await pipe.execute()
            if not job_dict:
                return None
            job_dict["id"] = job_id
            try:
                return InProcessJob.model_validate(job_dict)
            except ValidationError:
                dp.logger.error(
                    f"Validation error occurred while reading from redis! The following data was read instead of an InProcessJob object: {job_dict}. Continuing..."
                )
                return None

    async def abort_in_process_job(self, job_id: int):
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.hget(self.__get_job_key(job_id), "user_id")
            (user_id,) = await pipe.execute()
            pipe.hset(self.__get_job_key(job_id), "abort", "1")
            if user_id is not None:
                self.__send_event(pipe, user_id, SSEEvent.JOB_UPDATED, str(job_id))
            await pipe.execute()
            if user_id is None:
                raise Exception(f"Redis didn't return a user_id for the job with id {job_id}!")

    async def report_progress_of_in_process_job(self, job_id: int, progress: float):
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.hget(self.__get_job_key(job_id), "user_id")
            (user_id,) = await pipe.execute()
            pipe.hset(self.__get_job_key(job_id), "progress", str(progress))
            self.__send_event(pipe, user_id, SSEEvent.JOB_UPDATED, str(job_id))
            await pipe.execute()

    async def queue_contains_job(self, job_id: int) -> bool:
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.zmscore(self.__job_queue_sorted_set_name, [str(job_id)])
            ((score,),) = await pipe.execute()
            if score is None:
                return False
            return True

    def __send_event(self, pipe: Pipeline, user_id: int, event: SSEEvent, data: str):
        pipe.publish(self.__get_pubsub_channel(event, user_id), data)

    async def send_event(self, user_id: int, event: SSEEvent, data: str):
        async with self.client.pipeline(transaction=True) as pipe:
            self.__send_event(pipe, user_id, event, data)
            await pipe.execute()

    async def event_generator(self, user_id: int) -> AsyncGenerator[str, None]:
        async with self.client.pubsub() as pubsub:
            for event in SSEEvent:
                await pubsub.subscribe(self.__get_pubsub_channel(event, user_id))
            async for message in pubsub.listen():
                if message["type"] == "message":
                    event = message["channel"].split(":")[0]
                    data = message["data"]
                    yield f"event: {event}\ndata: {data}\n\n"
