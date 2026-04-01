import os
import pytest

from io import BytesIO
from httpx import codes

from .helper_functions import wait_for_job_assignment, wait_for_job_completion


def test_about(get_client):
    client = get_client[0]()
    response = client.get("/api/about")
    assert response.status_code == codes.OK
    assert response.headers.get("Content-Type") == "application/json"
    content = response.json()
    assert (
        content.get("description")
        == "A self-hostable platform on which users can create transcripts of their audio files (speech-to-text) using Whisper AI"
    )
    assert content.get("source_code") == "https://github.com/ssciwr/project-W/tree/main/backend"
    version_splitted = content.get("version").split(".")
    assert (
        version_splitted[0] != "0" or version_splitted[1] != "0"
    )  # make sure that version is larger than 0.0.x
    assert type(content.get("git_hash")) is str


def test_about_imprint(get_client):
    client = get_client[0]()
    response = client.get("/api/about")
    assert response.status_code == codes.OK
    assert response.headers.get("Content-Type") == "application/json"
    content = response.json()
    assert content.get("imprint").get("name") == "CI"
    assert content.get("imprint").get("email") == "ci@example.org"
    assert content.get("imprint").get("additional_imprint_html") == "<div>hello</div>"


@pytest.mark.parametrize(
    "backend",
    [
        (None,),
    ],
    indirect=True,
)
def test_full_workflow_simple(runner, get_logged_in_client):
    runner_name = "runner 1"
    with runner(runner_name, 100) as runner_id:
        client = get_logged_in_client()

        dummy_file = BytesIO(os.urandom(1024 * 1024))  # generate 1MiB random binary file

        files = {"audio_file": ("dummy-file", dummy_file, "audio/aac")}
        response = client.post("/api/jobs/submit_job", files=files)
        assert response.status_code == codes.OK
        job_id = response.json()

        response = client.get(
            "/api/jobs/count",
            params={
                "exclude_finished": False,
                "exclude_downloaded": False,
            },
        )
        assert response.status_code == codes.OK
        assert response.json() == 1

        response = client.get(
            "/api/jobs/get",
            params={
                "start_index": 0,
                "end_index": 9,
                "sort_key": "creation_time",
                "descending": True,
                "exclude_finished": False,
                "exclude_downloaded": False,
            },
        )
        assert response.status_code == codes.OK
        assert response.json() == [job_id]

        response = client.get("/api/jobs/info", params={"job_ids": job_id})
        assert response.status_code == codes.OK
        job_info = response.json()
        assert job_info[0].get("file_name") == "dummy-file"
        assert type(job_info[0].get("settings")) is dict
        assert type(job_info[0].get("creation_timestamp")) is str
        assert 0 <= job_info[0].get("progress") <= 100

        wait_for_job_assignment(job_id, client)
        response = client.get("/api/jobs/info", params={"job_ids": job_id})
        assert response.status_code == codes.OK
        job_info = response.json()
        assert 0 <= job_info[0].get("progress") <= 100
        assert job_info[0].get("runner_id") == runner_id
        assert job_info[0].get("runner_name") == runner_name
        assert (
            job_info[0].get("runner_source_code_url")
            == "https://github.com/ssciwr/project-W/tree/main/runner"
        )
        runner_version_splitted = job_info[0].get("runner_version").split(".")
        assert (
            runner_version_splitted[0] != "0" or runner_version_splitted[1] != "0"
        )  # make sure that version is larger than 0.0.x
        assert type(job_info[0].get("runner_git_hash")) is str

        wait_for_job_completion(job_id, client)
        response = client.get("/api/jobs/info", params={"job_ids": job_id})
        assert response.status_code == codes.OK
        job_info = response.json()
        assert job_info[0].get("progress") == 100
        assert type(job_info[0].get("finish_timestamp")) is str
        assert job_info[0].get("runner_id") == runner_id
        assert job_info[0].get("runner_name") == runner_name
        assert (
            job_info[0].get("runner_source_code_url")
            == "https://github.com/ssciwr/project-W/tree/main/runner"
        )
        runner_version_splitted = job_info[0].get("runner_version").split(".")
        assert (
            runner_version_splitted[0] != "0" or runner_version_splitted[1] != "0"
        )  # make sure that version is larger than 0.0.x
        assert type(job_info[0].get("runner_git_hash")) is str

        response = client.get(
            "/api/jobs/download_transcript",
            params={
                "job_id": job_id,
                "transcript_type": "as_txt",
            },
        )
        assert response.status_code == codes.OK
        assert (
            response.json()
            == """Space, the final frontier.
These are the voyages of the starship Enterprise.
Its five-year mission, to explore strange new worlds, to seek out new life and new civilizations, to boldly go where no man has gone before."""
        )
