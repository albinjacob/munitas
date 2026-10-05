"""Executing an uploaded agent's own code, sandboxed.

Two throwaway containers, not one. The first (`build_deps`) has ordinary
Docker bridge networking, with real internet access, so it can `pip
install` a project's declared dependencies; it never sees a dataset, a
credential, or `agentnet`. The second (`run_sandboxed`) is attached only to
`agentnet` (`docker-compose.yml`, `internal: true`, no gateway, proven to
have no internet route by `verify/v7_egress.py`) and mounts what the first
produced, read-only. Splitting the two is the only way to support arbitrary
dependencies without ever giving code that is about to hold a real dataset
credential a route off the machine.

No credential of this platform's own is ever handed to either container:
not `PG_DSN`, not an S3 admin key, nothing from this module's own
`config.py`. Everything the run container can do is limited to the same
`POST /credentials` request any other workload identity can make
(`agent/tools.py`'s pattern, proven against arbitrary out-of-process code
by `verify/v54_agent_run_scope.py`), asked over the network from inside the
sandbox itself, never handed to it in advance.

The run container's own contract with its code: environment variables in
(`MUNITAS_RUN_ID`, `MUNITAS_PRINCIPAL_ID`, `MUNITAS_TENANT_ID`,
`MUNITAS_PURPOSE`, `MUNITAS_DATASET_VERSION_ID`, `MUNITAS_ROLES`,
`MUNITAS_API`, `MUNITAS_RUN_SECRET`), one JSON object printed to stdout
before exit as the result. `MUNITAS_RUN_SECRET` is not a platform
credential in the sense the paragraph above means: it proves nothing about
storage or the API's own admin access, only that whatever calls
`POST /credentials` naming this run is the code this run actually launched,
not something else that merely learned the run's id (`agent_run.run_secret`
in `schema.sql`). A version declared `data_access = 'copy'` also gets `/data`,
read-only: the run's authorized dataset objects, staged by `stage_data`
before this container starts, at paths relative to the dataset version's
own storage prefix, alongside `/data/manifest.json` naming each file's
original key, byte size, and checksum. No SDK is shipped for this in v1:
the contract is documented, not wrapped, the same way
`scripts/admin/register-agent-version.py`'s own docstring already documents its raw
HTTP contract for external teams.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from pathlib import Path

import docker
import httpx
import requests
import urllib3.exceptions
from docker.errors import APIError, NotFound

from . import config

log = logging.getLogger("munitas.worker.sandbox")

RUN_IMAGE = "python:3.11-slim"
# `nobody:nogroup` on Debian (this image's base), present in every image
# derived from it including this minimal one, chosen because it is not this
# platform's own worker uid and owns nothing on the image or the mounted
# volumes: exactly what a container running someone else's uploaded code
# should run as. Only `run_sandboxed`'s container -- the one that actually
# executes an agent's own uploaded code -- runs as this; `build_deps` and
# `stage_data`'s fetch container run platform-authored or declared-dependency
# code, not an agent's own, and both need real write access to a host-mounted
# directory (`/deps`, `/out`) that this uid does not own.
SANDBOX_UID = "65534:65534"
BUILD_TIMEOUT_SECONDS = 120
# Matches agent.identity.Budget.max_seconds, for the same reason every other
# run ceiling in this platform lines up: one number to reason about, not two
# that happen to usually agree.
DEFAULT_RUN_TIMEOUT_SECONDS = 300
MEMORY_LIMIT = "512m"
CPU_LIMIT_NANO = int(1.0 * 1_000_000_000)  # 1 CPU

# 1GB, chosen from a real measurement against this stack, not a guess: a
# 1GB dataset shaped like a real one (many small files, not one blob)
# costs roughly 25-30 seconds to stage (network read ~93 MB/s, disk write
# to the staging volume ~67 MB/s, the slower of the two dominating),
# comfortably inside the 300s default run ceiling above.
MAX_STAGED_BYTES = 1024 * 1024 * 1024

# Generous headroom over the measured ~25-30s worst case, the same margin
# BUILD_TIMEOUT_SECONDS already gives arbitrary pip installs.
STAGE_TIMEOUT_SECONDS = 180

# Docker's blkio-weight range is 10-1000, default 500. Staging is bulk,
# background-shaped I/O; a below-default weight would mean it yields to
# whatever else the host disk is doing rather than competing evenly, which
# matters because this worker can run more than one sandboxed activity
# concurrently (no max_concurrent_activities=1 on the agent-run task
# queue, unlike the pipeline's GPU-bound one).
#
# Defined but not passed to stage_data's own _run_and_wait call below.
# Confirmed live against this host (`docker run --rm --blkio-weight 100
# alpine echo test`, independent of this module's own code) that this
# Docker Desktop/WSL2 backend does not expose the `io` cgroup controller
# blkio-weight needs: the container fails to start at all ("openat2
# /sys/fs/cgroup/.../io.weight: no such file or directory"), not a soft
# degradation. Applying it here would make every staged run fail
# outright on this host. Real host-specific evidence, not a guess.
STAGE_BLKIO_WEIGHT = 100


class SandboxBuildError(Exception):
    """Installing a project's own dependencies failed."""


class SandboxDataStagingError(Exception):
    """Fetching a run's authorized dataset objects failed, or the
    dataset version's declared size exceeded MAX_STAGED_BYTES."""


class AwaitingActivation(Exception):
    """The staging credential was allowed but is not in effect yet (HTTP 202).

    Not a failure: the run parks and the platform starts it again from
    staging once the access is active. Nothing of the agent's has run yet.
    """


def _is_wait_timeout(exc: Exception) -> bool:
    """Whether `container.wait(timeout=...)` raised because the container
    outlived its ceiling, as opposed to some other connection failure.

    Needed because the exception type differs by transport: over the Unix
    socket docker-py normally uses, a wait timeout surfaces as `requests.
    exceptions.ReadTimeout` directly. Over the named pipe it uses on
    Windows (confirmed against a live daemon on this machine, not assumed),
    the same condition instead surfaces as `requests.exceptions.
    ConnectionError`, wrapping a `urllib3.exceptions.ReadTimeoutError`
    several frames down. Treating every `ConnectionError` here as a timeout
    would misreport a genuinely dead daemon as "the code ran too long,"
    which is a different problem with a different fix, so this walks the
    cause chain and only calls it a timeout when a `ReadTimeoutError` is
    actually in it.
    """
    if isinstance(exc, requests.exceptions.ReadTimeout):
        return True
    seen: BaseException | None = exc
    for _ in range(6):
        if isinstance(seen, urllib3.exceptions.ReadTimeoutError):
            return True
        seen = seen.__cause__ or seen.__context__
        if seen is None:
            return False
    return False


def fetch_code(agent_id: str, version_id: str) -> bytes:
    response = httpx.get(
        f"{config.API}/agents/{agent_id}/versions/{version_id}/code",
        headers={"x-worker-token": config.WORKER_TOKEN},
        timeout=30.0, verify=config.api_verify(),
    )
    response.raise_for_status()
    return response.content


def extract_code(payload: bytes, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        archive.extractall(dest)
    return dest


def _run_and_wait(
    client: "docker.DockerClient",
    *,
    image: str,
    command: list[str],
    environment: dict,
    volumes: dict,
    network: str | None,
    timeout: int,
    label: str,
    run_id: str,
    blkio_weight: int | None = None,
    user: str | None = None,
    cap_drop: list[str] | None = None,
    security_opt: list[str] | None = None,
    read_only: bool = False,
    tmpfs: dict[str, str] | None = None,
) -> tuple[int | None, bytes, bool]:
    """Launch one container, wait up to `timeout`, force-kill on hang.

    Returns `(exit_code, stdout_bytes, timed_out)`. `exit_code` is `None`
    only when `timed_out` is `True`; a killed container never reports one.
    The container is always removed before this returns, success, failure,
    or hang alike: a sandboxed run leaving containers behind is exactly the
    kind of leak the plan's own verification step checks for.

    `create()` then `start()` separately, not `client.containers.run(...,
    detach=True)`: that convenience method creates and starts in one call,
    so a failure inside its own internal start step (confirmed live: an
    unsupported `blkio_weight` on this host's cgroup backend) raises
    before this function's own container variable is ever assigned, and
    the `finally` below never gets a chance to run. Separating the two
    means `container` exists, and gets cleaned up, even when `start()`
    itself is what fails.

    `user`/`cap_drop`/`security_opt`/`read_only`/`tmpfs` default to root,
    full capabilities, and a writable root filesystem -- this function's
    existing behaviour for every caller except `run_sandboxed`, which is
    the only one of the three container launches in this module that
    actually executes an agent's own uploaded code rather than
    platform-authored or declared-dependency code.
    """
    run_kwargs: dict = {}
    if blkio_weight is not None:
        run_kwargs["blkio_weight"] = blkio_weight
    if user is not None:
        run_kwargs["user"] = user
    if cap_drop is not None:
        run_kwargs["cap_drop"] = cap_drop
    if security_opt is not None:
        run_kwargs["security_opt"] = security_opt
    if read_only:
        run_kwargs["read_only"] = read_only
    if tmpfs is not None:
        run_kwargs["tmpfs"] = tmpfs
    container = client.containers.create(
        image,
        command=command,
        environment=environment,
        volumes=volumes,
        network=network,
        mem_limit=MEMORY_LIMIT,
        nano_cpus=CPU_LIMIT_NANO,
        labels={"munitas.sandbox": "true", "munitas.run_id": run_id},
        **run_kwargs,
    )
    timed_out = False
    exit_code: int | None = None
    try:
        container.start()
        try:
            result = container.wait(timeout=timeout)
            exit_code = result.get("StatusCode", 1)
        except (requests.exceptions.ReadTimeout,
                requests.exceptions.ConnectionError) as exc:
            if not _is_wait_timeout(exc):
                raise
            # Distinguishing this from any other failure matters: a hang is
            # not an error, it is silence, and the only fix is a ceiling
            # enforced from outside the thing that might not stop.
            timed_out = True
            log.warning(
                "%s: container %s exceeded its %ss ceiling; killing it",
                label, container.short_id, timeout,
            )
            try:
                container.kill()
            except NotFound:
                pass
        logs = container.logs(stdout=True, stderr=False)
    finally:
        try:
            container.remove(force=True)
        except NotFound:
            pass
    return exit_code, logs, timed_out


def reap_orphaned_containers() -> int:
    """Remove every container this worker process did not itself launch.

    Called once at worker startup, before any Temporal activity task is
    accepted. This worker is a single host process with no concurrent
    instances (python -m worker.main, run by hand), so any container
    carrying the munitas.sandbox label at this point in the process's
    life is, by definition, left over from a process that is no longer
    running. _run_and_wait's own `finally` block already removes its
    container in every ordinary outcome (success, failure, timeout); the
    only gap this closes is the worker process itself dying before that
    `finally` runs.

    Returns the number of containers removed, so the caller can log a
    clean "nothing to reap" versus an actual sweep.
    """
    client = docker.from_env()
    orphans = client.containers.list(
        all=True, filters={"label": "munitas.sandbox=true"}
    )
    for container in orphans:
        run_id = container.labels.get("munitas.run_id", "unknown")
        created = container.attrs.get("Created", "unknown")
        log.warning(
            "reaping orphaned sandbox container %s (run %s, created %s): "
            "left behind by a previous worker process",
            container.short_id, run_id, created,
        )
        try:
            container.kill()
        except NotFound:
            pass
        except APIError as exc:
            # 409 is Docker saying the container is not running, which is the
            # ordinary state of a leftover that finished by itself: there is
            # nothing to kill and it is removed next. Any other error is real.
            # Treating this one as fatal stopped the whole worker at start-up.
            if exc.status_code != 409:
                raise
        try:
            container.remove(force=True)
        except NotFound:
            pass
    return len(orphans)


def build_deps(code_dir: Path, deps_dir: Path, run_id: str,
                timeout: int = BUILD_TIMEOUT_SECONDS) -> Path | None:
    """Install a project's declared dependencies, if it has any.

    Runs on the ordinary Docker bridge network (real internet access),
    this step never touches `agentnet`, never sees a dataset version, and
    never sees a credential. It only ever reads `requirements.txt`.
    """
    if not (code_dir / "requirements.txt").exists():
        return None

    deps_dir.mkdir(parents=True, exist_ok=True)
    client = docker.from_env()
    exit_code, logs, timed_out = _run_and_wait(
        client,
        image=RUN_IMAGE,
        command=["pip", "install", "--quiet", "--no-input",
                 "--target", "/deps", "-r", "/code/requirements.txt"],
        environment={},
        volumes={
            str(code_dir): {"bind": "/code", "mode": "ro"},
            str(deps_dir): {"bind": "/deps", "mode": "rw"},
        },
        network=None,  # default bridge, never agentnet: this step needs internet
        timeout=timeout,
        label="dependency build",
        run_id=run_id,
    )
    if timed_out or exit_code != 0:
        tail = logs.decode("utf-8", errors="replace")[-2000:]
        raise SandboxBuildError(
            f"installing dependencies failed (exit={exit_code}, "
            f"timed_out={timed_out}): {tail}"
        )
    return deps_dir


def _staging_manifest(object_manifest: list[dict], storage_prefix: str) -> list[dict]:
    """Turn a dataset version's raw object_manifest into what the staging
    container needs: each entry's real S3 key, plus a relative path safe
    to write under /out, with the version's own storage_prefix stripped
    so the agent sees ordinary relative paths, not the platform's
    internal layout.

    Falls back to an entry's basename when its key does not start with
    the version's own prefix, which should not happen for anything sealed
    through the normal path but is handled rather than crashed on.
    """
    entries = []
    strip = f"{storage_prefix}/"
    for entry in object_manifest:
        key = entry["key"]
        relative = key[len(strip):] if key.startswith(strip) else key.rsplit("/", 1)[-1]
        entries.append({
            "key": key, "relative_path": relative,
            "bytes": entry.get("bytes"), "sha256": entry.get("sha256"),
        })
    return entries


def stage_data(params: dict, run_dir: Path,
                timeout: int = STAGE_TIMEOUT_SECONDS) -> Path | None:
    """Fetch this run's authorized dataset objects to local disk, for a
    sandboxed run whose agent_version declared data_access = 'copy'.

    Authorizes itself the same way agent/tools.py's _authorise already
    does for the native path: a real POST /credentials call using this
    run's own pinned dataset_version_id, never an admin key. Refuses,
    before fetching a single byte, a dataset version whose declared total
    size exceeds MAX_STAGED_BYTES, so an oversized version fails fast and
    named rather than filling the disk or stalling the run.

    Returns None when there is nothing to stage (no dataset_version_id on
    this run, or the version's manifest is empty), the same "nothing to
    do" shape build_deps already uses for a project with no
    requirements.txt.
    """
    dataset_version_id = params.get("dataset_version_id")
    if not dataset_version_id:
        return None

    version = httpx.get(
        f"{config.API}/dataset-versions/{dataset_version_id}",
        params={"tenant_id": params["tenant_id"]}, timeout=20.0,
        headers={"x-worker-token": config.WORKER_TOKEN},
        verify=config.api_verify(),
    )
    version.raise_for_status()
    info = version.json()
    object_manifest = info.get("object_manifest") or []
    if not object_manifest:
        return None

    total_bytes = sum(int(entry.get("bytes") or 0) for entry in object_manifest)
    if total_bytes > MAX_STAGED_BYTES:
        raise SandboxDataStagingError(
            f"dataset version {dataset_version_id} is {total_bytes} bytes, "
            f"over the {MAX_STAGED_BYTES} byte sandboxed-staging limit"
        )

    credential = httpx.post(f"{config.API}/credentials", json={
        "principal": params["principal_id"],
        "principal_kind": "workload",
        "roles": ["agent_runtime"],
        "tenant_id": params["tenant_id"],
        "dataset_version_id": dataset_version_id,
        "purpose": params["purpose"],
        "agent_run_id": params["run_id"],
        "run_secret": params.get("run_secret"),
    }, timeout=20.0, verify=config.api_verify())
    if credential.status_code == 202:
        raise AwaitingActivation(
            f"access to {dataset_version_id} is allowed and waiting to take effect"
        )
    if credential.status_code != 200:
        raise SandboxDataStagingError(
            f"could not obtain a staging credential for {dataset_version_id}: "
            f"HTTP {credential.status_code} {credential.text[:300]}"
        )
    grant = credential.json()

    staging_dir = run_dir / "stage"
    staging_dir.mkdir(parents=True, exist_ok=True)
    entries = _staging_manifest(object_manifest, info["storage_prefix"])
    (staging_dir / "manifest.json").write_text(json.dumps({"objects": entries}))

    data_dir = run_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # grant["endpoint"] is the authoritative source for where to actually
    # connect, correct for either backend. An earlier version of this
    # function hardcoded a worker-side constant instead, which only ever
    # coincidentally matched before R2 existed as a second possibility.
    stage_environment = {
        "MUNITAS_S3_ENDPOINT": grant["endpoint"],
        "MUNITAS_S3_ACCESS_KEY": grant["access_key"],
        "MUNITAS_S3_SECRET_KEY": grant["secret_key"],
        "MUNITAS_S3_BUCKET": grant["bucket"],
    }
    if grant.get("session_token"):
        stage_environment["MUNITAS_S3_SESSION_TOKEN"] = grant["session_token"]

    fetch_script = Path(__file__).parent / "sandbox_stage_fetch.py"
    client = docker.from_env()
    exit_code, logs, timed_out = _run_and_wait(
        client,
        image=RUN_IMAGE,
        command=["sh", "-c",
                 "pip install --quiet --no-input boto3 && python /stage/fetch.py"],
        environment=stage_environment,
        volumes={
            str(fetch_script): {"bind": "/stage/fetch.py", "mode": "ro"},
            str(staging_dir / "manifest.json"): {"bind": "/stage/manifest.json", "mode": "ro"},
            str(data_dir): {"bind": "/out", "mode": "rw"},
        },
        network=config.DATA_NETWORK,
        timeout=timeout,
        label="data staging",
        run_id=params["run_id"],
        # Not blkio_weight=STAGE_BLKIO_WEIGHT: confirmed live that this
        # host's Docker backend does not support it (see the constant's
        # own comment above). _run_and_wait's blkio_weight parameter
        # still exists for a host where it does.
    )
    if timed_out or exit_code != 0:
        tail = logs.decode("utf-8", errors="replace")[-2000:]
        raise SandboxDataStagingError(
            f"staging dataset objects failed (exit={exit_code}, "
            f"timed_out={timed_out}): {tail}"
        )
    return data_dir


def _last_json_object(text: str) -> dict | None:
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        return parsed if isinstance(parsed, dict) else None
    return None


def run_sandboxed(params: dict, code_dir: Path, deps_dir: Path | None,
                   entrypoint: str, data_dir: Path | None = None) -> dict:
    """Run the extracted code once, with no network beyond `agentnet`.

    Returns `{"status", "reason", "output", "exit_code", "timed_out"}`.
    `status` is `"succeeded"` only when the process exited 0 and printed a
    parseable JSON object; every other outcome (timeout, nonzero exit, no
    parseable output) is `"failed"` with a real reason, never silently
    reported as success.
    """
    client = docker.from_env()

    volumes = {str(code_dir): {"bind": "/code", "mode": "ro"}}
    python_path = "/code"
    if deps_dir is not None:
        volumes[str(deps_dir)] = {"bind": "/deps", "mode": "ro"}
        python_path = "/deps:/code"
    if data_dir is not None:
        volumes[str(data_dir)] = {"bind": "/data", "mode": "ro"}

    environment = {
        "MUNITAS_RUN_ID": params["run_id"],
        "MUNITAS_PRINCIPAL_ID": params["principal_id"],
        "MUNITAS_TENANT_ID": params["tenant_id"],
        "MUNITAS_PURPOSE": params["purpose"],
        "MUNITAS_ROLES": "agent_runtime",
        "MUNITAS_API": config.SANDBOX_API,
        "PYTHONPATH": python_path,
        # SANDBOX_UID has no real home directory entry, so anything that
        # assumes $HOME is writable (a library's own cache directory, most
        # often) needs pointing somewhere that actually is: the tmpfs below.
        "HOME": "/tmp",
    }
    if params.get("run_secret"):
        # Proof this container is the code this run actually launched, not
        # merely something that learned the run's id -- see
        # agent_run.run_secret's own comment in schema.sql. `None` only for
        # a run row from before this field existed.
        environment["MUNITAS_RUN_SECRET"] = params["run_secret"]
    if params.get("dataset_version_id"):
        environment["MUNITAS_DATASET_VERSION_ID"] = params["dataset_version_id"]

    # `or` rather than `.get(..., DEFAULT)`: the key is present with value
    # `None` whenever the API's optional `timeout_seconds` field was left
    # unset (StartAgentRun's default), and `.get` only falls back to its
    # default when the key is missing entirely, not when it's `None`.
    timeout = params.get("timeout_seconds") or DEFAULT_RUN_TIMEOUT_SECONDS
    exit_code, logs, timed_out = _run_and_wait(
        client,
        image=RUN_IMAGE,
        command=["python", f"/code/{entrypoint}"],
        environment=environment,
        volumes=volumes,
        network=config.AGENTNET,
        timeout=timeout,
        label=f"run {params['run_id']}",
        run_id=params["run_id"],
        # This is the one container of the three in this module that runs an
        # agent's own uploaded code, so it is the one that runs least
        # privileged: an unprivileged uid that owns nothing, every Linux
        # capability dropped, no privilege escalation even back to what the
        # image's own files would otherwise allow, and a filesystem that
        # cannot be changed anywhere except a small memory-backed /tmp for
        # code that legitimately expects to write a scratch file somewhere.
        # `/code`, `/deps` and `/data` are already read-only bind mounts
        # above regardless of uid; this closes the rest of the filesystem
        # and the container's own privileges around them.
        user=SANDBOX_UID,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"],
        read_only=True,
        tmpfs={"/tmp": "size=64m"},
    )

    if timed_out:
        return {
            "status": "failed",
            "reason": f"the run exceeded its {timeout}s wall-clock ceiling and was killed",
            "output": None, "exit_code": None, "timed_out": True,
        }

    text = logs.decode("utf-8", errors="replace")
    output = _last_json_object(text)

    if exit_code != 0:
        return {
            "status": "failed",
            "reason": f"the sandboxed process exited {exit_code}",
            "output": output, "exit_code": exit_code, "timed_out": False,
        }
    if output is None:
        return {
            "status": "failed",
            "reason": "the sandboxed process exited 0 but printed no parseable JSON result",
            "output": None, "exit_code": exit_code, "timed_out": False,
        }
    return {
        "status": "succeeded", "reason": None, "output": output,
        "exit_code": exit_code, "timed_out": False,
    }
