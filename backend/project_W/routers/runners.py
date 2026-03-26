from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from project_W_lib.models.request_models import (
    HeartbeatRequest,
    RunnerRegisterRequest,
    RunnerSubmitResultRequest,
)
from project_W_lib.models.response_models import (
    ErrorResponse,
    HeartbeatResponse,
    RegisteredResponse,
    RunnerJobInfoResponse,
)

import project_W.dependencies as dp

from ..models.internal_models import OnlineRunner
from ..security.auth import (
    online_runner_dependency_responses,
    runner_dependency_responses,
    validate_online_runner,
    validate_runner,
)

router = APIRouter(
    prefix="/runners",
    tags=["runners"],
    responses=runner_dependency_responses,
)


@router.post(
    "/register",
    responses={
        403: {
            "model": ErrorResponse,
            "description": "This runner is currently already registered as online",
        },
    },
)
async def register(
    runner_id: Annotated[int, Depends(validate_runner)],
    runner_data: RunnerRegisterRequest,
) -> RegisteredResponse:
    """
    Registers the runner with the given runner_id as online.
    Starting from the registration, the runner must periodically send
    heartbeat requests to the manager, or it may be unregistered.
    Returns the runner id on success.
    """
    if (await dp.ch.get_online_runner_by_id(runner_id)) is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This runner is already registered as online!",
        )

    token = await dp.ch.register_new_online_runner(runner_id, runner_data)

    await dp.ch.assign_queue_job_to_runner_if_possible()

    return RegisteredResponse(id=runner_id, session_token=token.root.get_secret_value())


@router.post("/unregister")
async def unregister(
    runner_id: Annotated[int, Depends(validate_runner)],
) -> str:
    """
    Unregisters an online runner. This will mark the runner as offline and no heartbeat or similar request will be possible anymore until another register request was performed.
    """
    await dp.ch.unregister_online_runner(runner_id)

    return "Success"


@router.get(
    "/retrieve_job_info",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "No job assigned or job not in database",
        },
        405: {
            "model": ErrorResponse,
            "description": "The assigned job was aborted by the user",
        },
        **online_runner_dependency_responses,
    },
)
async def retrieve_job_info(
    online_runner: Annotated[OnlineRunner, Depends(validate_online_runner)],
) -> RunnerJobInfoResponse:
    """
    The runner can retrieve metadata about the job that was assigned to it. This includes e.g. the job settings. The runner should call this route BEFORE it calls retrieve_job_audio to first make sure it can process the job. retrieve_job_info doesn't mark the job as running yet, only retrieve_job_audio will do that.
    """
    if online_runner.assigned_job_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This runner has currently no job assigned",
        )
    if (job := await dp.db.get_job_by_id(online_runner.assigned_job_id)) is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The job id assigned to this runner doesn't appear in the database!",
        )
    if job.aborting:
        raise HTTPException(
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
            detail="This job was aborted by a user. Can't get job info of an aborted job!",
        )
    if (job_settings := await dp.db.get_job_settings_by_job_id(job.id)) is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The job id of this runner doesn't appear in the database!",
        )
    return RunnerJobInfoResponse(id=job.id, settings=job_settings)


@router.post(
    "/retrieve_job_audio",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "No job assigned",
        },
        405: {
            "model": ErrorResponse,
            "description": "The assigned job was aborted by the user",
        },
        **online_runner_dependency_responses,
    },
)
async def retrieve_job_audio(
    online_runner: Annotated[OnlineRunner, Depends(validate_online_runner)],
) -> StreamingResponse:
    """
    The runner streams the audio binary data of the job it got assigned over this route.
    Additionally this route will mark the job a currently being processed by this runner.
    Before calling this route the runner should have called retrieve_job_info first.
    """
    if online_runner.assigned_job_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This runner has currently no job assigned",
        )
    if (job := await dp.db.get_job_by_id(online_runner.assigned_job_id)) is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The job id assigned to this runner doesn't appear in the database!",
        )
    if job.aborting:
        raise HTTPException(
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
            detail="This job was aborted by a user. Can't get job audio of an aborted job!",
        )

    if not online_runner.in_process:
        await dp.ch.mark_job_of_runner_in_progress(online_runner.id)

    return StreamingResponse(dp.db.get_job_audio(job.id), media_type="audio/")


@router.post(
    "/submit_job_result",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Runner not processing a job",
        },
        **online_runner_dependency_responses,
    },
)
async def submit_job_result(
    online_runner: Annotated[OnlineRunner, Depends(validate_online_runner)],
    submitted_data: RunnerSubmitResultRequest,
    background_tasks: BackgroundTasks,
) -> str:
    """
    The runner submits the result of processing the job it got assigned over this route. The result can either be that the job failed (in which case the runner submits an error message) or that the job was successful (in which case the runner submits the transcript in all possible formats). This route will mark the job as failed or successful and notify the user over email if they activated email notifications for this job.
    """
    if not online_runner.in_process or online_runner.assigned_job_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This runner is currently not processing a job!",
        )

    if not (settings := await dp.db.get_job_settings_by_job_id(online_runner.assigned_job_id)):
        raise Exception("Trying to submit job with an non-existing id!")
    if not (in_process_job := await dp.ch.get_in_process_job(online_runner.assigned_job_id)):
        raise Exception("Trying to submit job that doesn't exist in Redis!")
    if not (user := await dp.db.get_user_by_id(in_process_job.user_id)):
        raise Exception("Trying to submit a job that belongs to a non-existing user!")

    if submitted_data.error_msg is not None:
        job_failed = True
    elif submitted_data.transcript is not None:
        job_failed = False
    else:
        raise Exception(
            "Pydantic model validation failed, job submission has neither error_msg nor a transcript attached to it"
        )

    # processing the result can take some time for larger jobs, especially the database operations
    # so do this as a background task
    async def process_job_result():
        assert online_runner.assigned_job_id is not None
        if job_failed:
            assert submitted_data.error_msg is not None
            await dp.db.finish_failed_job(
                online_runner.assigned_job_id, submitted_data.error_msg, online_runner
            )
        else:
            assert submitted_data.transcript is not None
            await dp.db.finish_successful_job(online_runner, submitted_data.transcript)

        await dp.ch.finish_job_of_online_runner(online_runner.assigned_job_id)

        if settings.email_notification:
            if job_failed:
                assert submitted_data.error_msg is not None
                await dp.smtp.send_job_failed_email(
                    user.email, in_process_job.id, submitted_data.error_msg, dp.config.client_url
                )
            else:
                await dp.smtp.send_job_success_email(
                    user.email, in_process_job.id, dp.config.client_url
                )

    background_tasks.add_task(process_job_result)

    await dp.ch.unassign_current_job_from_online_runner(online_runner)
    await dp.ch.assign_queue_job_to_runner_if_possible()

    return "Success"


@router.post(
    "/heartbeat",
    responses=online_runner_dependency_responses,
)
async def heartbeat(
    online_runner: Annotated[OnlineRunner, Depends(validate_online_runner)], req: HeartbeatRequest
) -> HeartbeatResponse:
    """
    The heartbeat route that the runner has to periodically call to not be unregistered automatically by the backend. Over the response of this route the runner will also be notified about a new job that it got assigned or an abort request for a job the runner is currently processing.
    """
    await dp.ch.reset_runner_expiration(online_runner.id)

    if online_runner.in_process and online_runner.assigned_job_id is not None:
        in_process_job = await dp.ch.get_in_process_job(online_runner.assigned_job_id)
        if in_process_job is None:
            raise Exception(
                "Redis data invalid: OnlineRunner has in_process_job set but the jobs key doesn't exist"
            )
        if in_process_job.abort:
            return HeartbeatResponse(abort=True, job_assigned=False)
        if in_process_job.progress != req.progress:
            await dp.ch.report_progress_of_in_process_job(
                online_runner.assigned_job_id, req.progress
            )
    if online_runner.assigned_job_id:
        return HeartbeatResponse(abort=False, job_assigned=True)
    return HeartbeatResponse(abort=False, job_assigned=False)
