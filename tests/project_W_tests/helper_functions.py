import httpx
from time import sleep


def wait_for_backend(base_url: str, timeout: int = 10):
    for _ in range(timeout):
        try:
            httpx.get(f"{base_url}/api/about", verify=False).raise_for_status()
            return
        except httpx.HTTPError:
            sleep(1)
    raise TimeoutError("Server did not become healthy in time.")

def wait_for_job_assignment(job_id: int, client: httpx.Client, timeout: int = 10):
    for _ in range(timeout):
        response = client.get("/api/jobs/info", params={"job_ids": job_id})
        response.raise_for_status()
        job_info = response.json()
        if job_info[0].get("runner_id") is not None:
            return
        sleep(1)
    raise TimeoutError("Runner was not assigned to the job in time.")

def wait_for_job_completion(job_id: int, client: httpx.Client, timeout: int = 20):
    for _ in range(timeout):
        response = client.get("/api/jobs/info", params={"job_ids": job_id})
        response.raise_for_status()
        job_info = response.json()
        if job_info[0].get("step") == "success":
            return
        sleep(1)
    raise TimeoutError("Job was not completed in time.")
