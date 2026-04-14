# Final Exam — Theoretical Questions

## 3.1 API Design

### Question 1

The 7 fields in the `DiagnoseRequest` model (`api/src/app.py`, lines 42–49) are:

1. `piece_id` (string) — identifies the piece
2. `die_matrix` (integer) — determines which reference profile to use
3. `lifetime_2nd_strike_s` (float, optional)
4. `lifetime_3rd_strike_s` (float, optional)
5. `lifetime_4th_strike_s` (float, optional)
6. `lifetime_auxiliary_press_s` (float, optional)
7. `lifetime_bath_s` (float, optional)

Cumulative timestamps are the right choice because they are what the PLC actually records — each value represents seconds elapsed since the billet left the furnace. Accepting cumulative input means the client sends raw sensor data without any pre-processing. If we accepted partial times instead, every client would need to implement the subtraction logic from §1.5 themselves, which risks inconsistency — different clients could compute partials differently, or handle nulls wrong. By keeping that derivation inside `diagnose()` (via `_safe_subtract()` in `api/src/diagnose.py`), there is a single source of truth for how partials are calculated, including how null values propagate between adjacent segments.

This set is the minimum necessary because each of the 5 partial times in the response requires exactly one or two of these cumulative values. `furnace_to_2nd_strike` equals `lifetime_2nd_strike_s` directly. The other four are differences between consecutive cumulatives (e.g., `lifetime_3rd_strike_s - lifetime_2nd_strike_s`). Removing any one cumulative field would make it impossible to compute at least one partial time. `piece_id` is needed for the response, and `die_matrix` is needed to look up the correct reference values in `reference_times.json`. No field is redundant.

### Question 2

In `api/src/app.py` (lines 21–27), the `lifespan()` async context manager loads `reference_times.json` into `app.state.reference_times` once when the FastAPI application starts. Every subsequent request to `POST /diagnose` reads from this in-memory dict — no disk I/O at all.

This makes sense for a containerized deployment because the container image is immutable. `reference_times.json` is copied into the image at build time (`COPY reference_times.json .` in the Dockerfile, line 12), and its contents cannot change while the container is running. Reading the same file from disk on every request would waste I/O for data that is guaranteed to be identical every time. Loading once means the data sits in memory and lookups are instant.

This also aligns with how containers are supposed to work: if the reference data needs to change (e.g., new medians after recalibrating the line), you don't modify a running container — you rebuild the image with the updated JSON, push it to ECR, and deploy a new task. This is the principle of immutable infrastructure: configuration is baked into the image, not patched at runtime. Re-reading the file on every request would only make sense if the file could be updated externally (e.g., mounted from a volume), which is not the case here.

There is also a practical performance argument. In our Fargate deployment (256 CPU units, 512 MiB memory), the container has minimal resources. Avoiding repeated filesystem calls — even for a small file — keeps request latency predictable and purely CPU-bound (just the `diagnose()` computation and JSON serialization).

## 3.2 Containerization And Deployment

### Question 1

```
FROM python:3.13-slim
```
Base image. `python:3.13` matches our `requires-python = ">=3.13"` in `pyproject.toml`. The `-slim` variant is based on Debian but strips out build tools, documentation, and other extras not needed at runtime — roughly 150 MB vs ~1 GB for the full image. Our dependencies (FastAPI, uvicorn, pydantic) all ship as pre-built wheels, so we don't need the build tools that the full image includes. Slim is sufficient and keeps the image small, which matters for ECR push times and Fargate pull times.

```
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
```
Multi-stage copy that pulls just the `uv` and `uvx` binaries from the official uv image. This gives us the `uv` package manager inside the container without installing it through pip or curl, and without pulling in the full uv image as a layer.

```
WORKDIR /app
```
Sets the working directory. All subsequent `COPY`, `RUN`, and `CMD` instructions operate relative to `/app`.

```
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
```
This is the dependency caching layer. By copying only the dependency files first and running `uv sync`, Docker caches this layer. As long as `pyproject.toml` and `uv.lock` don't change, Docker reuses this cached layer on subsequent builds — even if our source code changes. `--frozen` ensures the lockfile is used exactly as-is (no re-resolution), which guarantees reproducible installs. `--no-dev` excludes the `dev` dependency group (pytest), since the runtime container doesn't need testing tools.

```
COPY src/ src/
COPY reference_times.json .
```
Copies the application code and the reference data file into the image. These are placed after the dependency layer so that code changes don't invalidate the cached dependency installation. `reference_times.json` is the per-matrix median reference times that `diagnose()` needs — it gets loaded once at startup by the `lifespan()` context manager in `app.py`. A `.dockerignore` file excludes `tests/`, `scripts/`, `.venv/`, and validation artifacts from the build context, so they never reach the Docker daemon — this keeps the build fast and avoids accidentally leaking test data into the image.

```
EXPOSE 80
```
Documents that the container listens on port 80. This is metadata for tooling and developers — it doesn't actually open the port. The actual port binding happens in the `CMD` below and in the ECS task definition / security group configuration.

```
CMD ["uv", "run", "uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "80"]
```
The default command when the container starts. `uv run` activates the virtual environment that `uv sync` created, then launches `uvicorn` pointing to our FastAPI `app` object in `src/app.py`. `--host 0.0.0.0` binds to all network interfaces — necessary because the default `127.0.0.1` would only be reachable from inside the container, not from the Fargate network interface that receives external traffic. Port 80 is the standard HTTP port, matching what the security group allows inbound.

### Question 2

**Option 1: AWS Lambda (with API Gateway)**

*Advantage:* Lambda scales to zero — when nobody is calling the API, there are no running resources and no cost. In our case, the diagnose API only needs to handle exam verification requests, so it would sit idle most of the time. With Fargate, our task runs continuously at 256 CPU / 512 MiB even when no one is sending requests.

*Disadvantage:* Cold starts. When a Lambda function hasn't been invoked recently, the first request has to spin up a new execution environment — this can add several hundred milliseconds to seconds of latency, especially for a Python runtime that needs to load FastAPI and our reference data. With our Fargate task, the container is always warm and `reference_times.json` is already loaded in memory via the `lifespan()` handler, so every request gets consistent low latency. There is also the packaging overhead: we would need to use something like Mangum to adapt our FastAPI app to Lambda's event handler interface, and we'd need an API Gateway in front of it to receive HTTP requests — additional infrastructure that Fargate doesn't require.

**Option 2: EC2**

*Advantage:* Full control over the instance. We could SSH into the machine, inspect logs directly, install debugging tools, choose any instance type, and even run multiple services on the same host. If we needed to troubleshoot a production issue (e.g., why `reference_times.json` isn't loading), we could inspect the filesystem in real time. With Fargate, we have no shell access to the running container — we can only see what gets written to CloudWatch logs.

*Disadvantage:* Operational burden. With EC2, we are responsible for everything the platform doesn't handle: patching the OS, configuring security updates, setting up a process manager (like systemd) to keep uvicorn running after crashes, and configuring health checks manually. With Fargate, the platform handles the host — we just provide the Docker image and task definition. In our deployment, we went from `docker push` to a running public API in a single `aws ecs run-task` command. With EC2, we'd need to launch the instance, install Docker (or Python + uv directly), configure security groups, pull the image, run it, and set up monitoring ourselves.

## 3.3 Testing And Extensibility

### Question 1

Testing `diagnose()` as a pure function means our tests import it directly and call it with plain Python dicts — no HTTP server, no network, no Docker container. In `api/tests/test_diagnose.py`, each test constructs a piece dict, passes it to `diagnose()` along with `reference_times` and `cause_table`, and asserts on the returned dict. This design choice has several concrete benefits over testing through HTTP:

**Failure isolation.** If a test through HTTP fails, the bug could be anywhere: in the diagnosis logic, in Pydantic's request parsing, in FastAPI's routing, in JSON serialization, in uvicorn's request handling, or even in the network layer. When a pure function test fails, the bug is in `diagnose()` — there is nothing else involved. Our 24 matrix tests and 10 golden tests are specifically verifying the delay-detection rules from §1.3, so we want failures to point directly at the logic, not at plumbing.

**No infrastructure dependency.** Our tests run with just `uv run pytest` — no uvicorn server to start, no Docker container to build, no port to bind. This means any developer can clone the repo and run the full test suite in under a second (our 46 tests run in 0.05s). If we tested through HTTP, we'd need a running server, which introduces setup/teardown complexity, port conflicts, and flaky failures from timing issues.

**Separation of concerns.** The exam deliberately asks us to keep the logic in a pure function separate from the route handler (§2.2). Testing the pure function validates this separation — if `diagnose()` were entangled with FastAPI (reading from `Request` objects, accessing `app.state` directly), it couldn't be tested this way. The fact that our tests work without importing anything from FastAPI proves the function is truly isolated. The route handler in `app.py` is just a thin wrapper: validate `die_matrix`, call `diagnose()`, return the result. That wrapper is so simple it barely needs its own tests.

**Determinism.** Pure function tests have no external state. Same input always produces same output. HTTP tests can fail intermittently due to port conflicts, slow startup, connection timeouts, or OS-level resource limits. For the golden test, which compares floating-point values after rounding to 1 decimal, we need exact reproducibility — any non-determinism would make the test unreliable.

### Question 2

The key design insight is that our system is data-driven: `diagnose()` doesn't hardcode any matrix-specific logic. It looks up `reference_times[str(die_matrix)]` dynamically, and the cause table is segment-based, not matrix-based. So supporting a new matrix requires no changes to the diagnosis logic or the route handler — only data files, tests, and redeployment.

Here is every change, in order:

**1. Collect production data for matrix 6001.** Before anything else, we need actual production readings from the forging line running with the new die tooling. These are the cumulative timing values for enough pieces to compute stable medians — the same kind of data that's in our gold parquet.

**2. Update `reference_times.json`.** Compute the 5 median partial times for matrix 6001 from the new production data. Our `api/scripts/compute_references.py` already handles this automatically — it groups by `die_matrix` and computes medians per segment. If the new data is added to the parquet file, re-running the script produces an updated JSON with a new `"6001": { ... }` entry alongside the existing four matrices.

**3. No changes to `api/src/diagnose.py`.** The function looks up references with `refs = reference_times[str(die_matrix)]` (line 59). As long as `"6001"` exists as a key in the dict, the logic works. The cause table is keyed by segment, not by matrix — the same 5 segments apply regardless of which tooling produced the piece.

**4. No changes to `api/src/app.py`.** The unknown-matrix validation on line 58 checks `str(body.die_matrix) not in reference_times`. Since the loaded JSON now includes `"6001"`, requests with `die_matrix: 6001` pass validation automatically.

**5. Update `api/tests/test_diagnose.py`.** Add `6001` to the parametrize decorator on line 95:

```python
@pytest.mark.parametrize("matrix", [4974, 5052, 5090, 5091, 6001])
```

This single change generates 6 new unit tests (all-OK + 5 single-segment scenarios) automatically — the `build_piece()` helper constructs cumulative times from the reference values, so it works for any matrix present in `reference_times.json`. The same line appears in the extra-credit tests (`test_multi_segment_delay`, `test_null_propagation`, `test_sensor_anomaly`), so updating those parametrize lists adds 3 more tests. Total: 9 new tests from changing a few lists.

**6. Update validation set (optional but recommended).** Add one or two pieces for matrix 6001 to `validation_pieces.csv` and re-run `api/scripts/generate_validation.py` to regenerate `validation_expected.json`. This extends the golden test to cover the new matrix.

**7. Rebuild and redeploy.** The updated `reference_times.json` must be baked into a new Docker image since it's bundled at build time (`COPY reference_times.json .` in the Dockerfile). The steps are:

```bash
cd api
docker build --platform linux/amd64 -t diagnose-api .
docker tag diagnose-api:latest <account>.dkr.ecr.eu-west-1.amazonaws.com/diagnose-api:latest
docker push <account>.dkr.ecr.eu-west-1.amazonaws.com/diagnose-api:latest
aws ecs run-task --cluster diagnose-api-cluster --task-definition diagnose-api-task ...
```

A new Fargate task pulls the updated image. The `lifespan()` handler loads the new JSON at startup, and the API now accepts `die_matrix: 6001`.

The fact that zero lines of logic code change — only a data file and test parametrization lists — shows that the design separates data from behavior. Adding a matrix is a configuration change, not a code change.
