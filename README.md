# Video Studio — FastAPI on Railway

A small, deployable video job service for a trusted hackathon team. Python 3.11+
(Docker uses 3.12), FastAPI, Uvicorn, SQLite, aiofiles, FFmpeg, and a single HTML page.
No Node build, Redis, Celery, or external database is required.

## Read this first

**Background tasks cannot make uploading 2 GB instantaneous.** They only move the
processing work out of the HTTP request. Railway currently requires request bodies
to finish uploading within five minutes. The included browser therefore reserves a
job immediately, sends the video as sequential **10 MiB HTTP requests**, and returns
to polling as soon as the final chunk is saved. Each chunk must still finish within
the gateway deadline; the browser times out each request at four minutes and retries.

The maximum file size is **2 GiB (2,147,483,648 bytes)**, which also covers decimal
2 GB. Incoming bytes go directly into `/app/storage/<uuid>/input`, with at most a
10 MiB application write buffer per active upload plus transport buffers. No whole
video `.read()`, multipart spool, or second upload copy is used.

**Deployment scope: exactly one Railway replica and one Uvicorn worker.** SQLite
persists job metadata; an asyncio queue feeds one separate Python processor at a
time. This is a hardened single-instance hackathon deployment, not a distributed
queue or a multi-tenant service. A persistent volume is required for restart recovery.

## Files

| File | Purpose |
| --- | --- |
| `main.py` | API, streaming writes, resource admission, durable jobs, worker, retention |
| `processor.py` | Working FFmpeg demo and the custom processing function |
| `static/index.html` | Drag/drop, upload percentages, retry/resume, polling, downloads |
| `Dockerfile` | Python slim, FFmpeg, libsm6, privilege dropping, signal handling |
| `start.sh` | Fix the mounted directory ownership, then run as UID 10001 |
| `requirements.txt` | Pinned runtime packages |
| `Procfile` | `web: python main.py`; Docker uses the equivalent CMD |
| `requirements-dev.txt`, `tests/` | Automated integration and full-size upload checks |
| `reference/solution.py` | Your original attachment, unchanged and not imported by the app |

## Run locally on your Mac

1. Install Python 3.11+ and FFmpeg. With Homebrew:

   ```bash
   brew install python@3.12 ffmpeg
   ```

2. Extract this project, open Terminal in the `video-worker` folder, and install:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install -r requirements.txt
   ```

3. Start the server with local storage:

   ```bash
   export STORAGE_DIR="$PWD/storage"
   python main.py
   ```

4. Open <http://localhost:8000>, select an MP4/MOV, and click **Upload & process**.
   The demo waits ten seconds, creates a real H.264/AAC MP4 preview of the first
   ten seconds, and creates JSON with input metadata and an empty events list.
   **It does not run event detection or produce a full-length processed video.**

5. Downloads appear when both files exist and the processor exits successfully.

On Ubuntu/Debian, install `python3-venv ffmpeg libsm6 libxext6` using apt instead of
Homebrew. On Windows use WSL2 or Docker; the process-group and file-lock handling
targets Linux/macOS. Do not run with `--reload` during a real upload/processing job.

### Run the exact container locally

```bash
docker build -t video-studio .
docker volume create video-storage
docker run --rm -p 8000:8000 \
  -e PORT=8000 \
  --mount source=video-storage,target=/app/storage \
  video-studio
```

For password protection, set `APP_PASSWORD` in your shell and add `-e APP_PASSWORD`
to `docker run`. The browser will prompt for HTTP Basic credentials (username
`admin` by default). Use HTTPS for remote access.

## Deploy directly to Railway

1. **Create a GitHub repository** containing the project files. The Dockerfile,
   `main.py`, and `requirements.txt` must be at the repository root. Do not commit
   `.env`, uploads, `.venv`, or model secrets. If using a monorepo, configure the
   service Root Directory to the folder containing these files.

2. In Railway choose **New Project → Deploy from GitHub repo**, select the repo,
   and let Railway detect the Dockerfile. No custom build command is needed.
   Leave the Start Command unset so Docker's `python main.py` CMD is used. The
   included Procfile documents the same command; FFmpeg comes from the Dockerfile.

3. **Attach a persistent volume to this service at `/app/storage`.** Do this before
   accepting uploads. Size it for input, output, and retained jobs: four simultaneous
   maximum-size jobs reserve about 16 GiB plus a 512 MiB safety margin. A 20 GiB
   volume is a practical starting allocation for those defaults; smaller volumes
   are valid with fewer job slots, but admission can return HTTP 507. Existing
   outputs also consume capacity. Choose a Railway plan that supports your needs.

4. Set these service variables:

   | Variable | Value |
   | --- | --- |
   | `STORAGE_DIR` | `/app/storage` |
   | `APP_USERNAME` | `admin` or your preferred username |
   | `APP_PASSWORD` | A long random password, entered in Railway's Variables UI |
   | `FORWARDED_ALLOW_IPS` | `*` for this service behind Railway's trusted edge |
   | `MAX_ACTIVE_JOBS` | `4` (lower it for a smaller volume) |
   | `RETENTION_SECONDS` | `86400` (24 hours) |

   Generate a password locally with
   `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
   Railway supplies `PORT`; the server binds `0.0.0.0:$PORT`. You do not need to set
   `PORT` yourself. The Docker entrypoint handles a fresh root-owned volume and
   drops to `appuser`; `RAILWAY_RUN_UID=0` is not needed for this image's default.

5. In service deployment settings set **Healthcheck Path = `/healthz`**, select
   an on-failure restart policy, and keep **one replica in one region**. Do not
   override the start command with multiple Uvicorn/Gunicorn workers. A storage
   lock deliberately refuses a second process. Disable Serverless/App Sleeping
   for this service so background work is not paused when the browser closes.

6. Under **Settings → Networking → Public Networking**, choose **Generate Domain**.
   Open the HTTPS domain and enter your Basic credentials. The HTML and API share
   this origin, so CORS configuration is unnecessary for the bundled frontend.
   Set `ALLOWED_ORIGINS=https://your-other-frontend.example` only if you intentionally
   host another frontend. Do not set it to `*`.

7. Deploy/redeploy with the volume and variables attached. Confirm the deployment
   logs show successful startup. Upload a short clip first, check both downloads,
   then try a large file over your actual connection. Observe Railway RAM, CPU,
   and disk usage before admitting a crowd. Custom ML processing will need more
   resources than the demo; test it with your real model and videos.

**No `railway.json` is required.** This project uses the requested Procfile option
and Docker CMD. Set health/scale/storage settings in the dashboard as described.
The Docker image was prepared here, but a hosted Railway deployment still needs
your Railway account, repository, and configured volume.

## API and upload protocol

All routes except `/healthz` use HTTP Basic authentication when `APP_PASSWORD` is
set. Without it the service is public. Use the same credentials for downloads.
UUIDs prevent guessable filenames; they are not per-user ownership checks. Everyone
with the shared password and a job ID can access that job.

| Method / path | Request | Response |
| --- | --- | --- |
| `GET /` | None | HTML page |
| `GET /healthz` | None | Worker liveness; 200 or 503 |
| `GET /api/config` | None | Upload size, chunk size, retention |
| `POST /api/uploads` | JSON `{ "filename": "clip.mp4", "size": 12345 }` | 201; job ID immediately, before video upload |
| `POST /api/upload?job_id=UUID&offset=0` | Raw binary, at most 10 MiB per request | 202; `{ "job_id": "...", "received": 10485760, "queued": false }` |
| `POST /api/upload?filename=clip.mp4&total_size=N` | Raw binary entire file | 202 after file is saved; same JSON, `queued: true` |
| `GET /api/status/UUID` | None | Status, integer progress, phase, committed byte count, error |
| `GET /api/download/UUID/json` | None | `result.json` attachment |
| `GET /api/download/UUID/mp4` | None | `processed_video.mp4` attachment; Range requests supported |

Use **`Content-Type: application/octet-stream`** for the upload body. This API
intentionally does not accept `multipart/form-data`: ordinary FastAPI `UploadFile`
parsing spools before the route runs and could create a second full disk copy.
`python-multipart` is included as requested but is not used on this upload path.

1. Reserve a job with `POST /api/uploads`; this checks size, extension, capacity,
   and disk reservations before transferring the video.
2. Send consecutive chunks to `POST /api/upload`, setting `offset` to the last
   acknowledged `received` byte count. Each request streams directly to disk.
3. The last request enqueues the job automatically. No separate finalize call.
4. On a lost response query `/api/status/UUID` before resending. A stale offset
   receives HTTP 409 and is never appended twice. A failed/partial request is
   rolled back to the last acknowledged offset. Do not upload two chunks of one
   job simultaneously. Chunk requests can use HTTP chunked transfer without a
   Content-Length header; limits are checked against actual received bytes too.
5. Poll every two seconds until completed or failed. Fetch download links only
   after completion.

For a small clip, the one-request alternative is convenient:

```bash
curl -u admin \
  -H 'Content-Type: application/octet-stream' \
  --data-binary @clip.mp4 \
  'https://YOUR-RAILWAY-DOMAIN/api/upload?filename=clip.mp4&total_size=EXACT_BYTE_COUNT'
```

Replace `EXACT_BYTE_COUNT` with the actual file length and use your domain.
`curl -u admin` prompts for the password. Omit `-u admin` for an unprotected local
instance. This one-shot route still has the five-minute upload limit; use the
included chunking frontend for large files.

Example status:

```json
{
  "status": "processing",
  "progress": 0,
  "phase": "processing",
  "received": 12345,
  "size": 12345,
  "error": null,
  "expires_at": null
}
```

`status` is always `pending`, `processing`, `completed`, or `failed`. While receiving
bytes it is `pending` with `phase: "uploading"`. Upload percentages are calculated
from XMLHttpRequest's transmitted byte count plus previously acknowledged chunks,
displayed to two decimals. The server acknowledges only after flush/fsync and a
metadata commit. **Processing progress is deliberately 0 until completion, then
100**: an arbitrary three-argument custom function cannot report a genuine percent.
The UI shows an indeterminate processing message instead of fabricated progress.

## Integrate your processor and the attached solution.py

Replace `process_video` in `processor.py`, keeping this exact interface:

```python
def process_video(input_path: str, output_json_path: str, output_mp4_path: str):
```

Read from `input_path`, write valid JSON to `output_json_path`, write a playable MP4
to `output_mp4_path`, and raise an exception on failure. Both outputs must be
non-empty. No need to change API routes or frontend. Long synchronous processing
is fine: the queue runs this module in a separate subprocess. Files are hidden
from downloads until the processor succeeds.

Your original file is preserved in `reference/solution.py`. It imports modules
that were not included: `src.config`, `src.risk`, `src.pipeline`, and `src.events`.
Its `detect_events(video_path)` only returns an event list; it does not create
the output video. **The supplied attachment alone cannot implement a working
analysis pipeline, and this project does not pretend that it can.**

To integrate the real project:

1. Add the complete `src/` package, configuration, model weights, and their Python
   dependencies. Copy `reference/solution.py` to the project root as `solution.py`.
2. Add `COPY src ./src` and `COPY solution.py ./` to the Dockerfile, plus explicit
   model/config copy instructions appropriate to your project. Check licensing
   and model requirements before deploying them.
3. Inside `process_video`, import `detect_events` from `solution`, call
   `events = detect_events(input_path)`, and write it into your JSON output.
4. Implement your real rendering/annotation step that writes `output_mp4_path`.
   The demo FFmpeg block can generate a preview during development, but it is
   not a substitute for your full-video output algorithm.
5. Test actual inputs before publishing. In the supplied `RiskEstimator`, call
   `reset(meta)` before `step`; validate that both metadata FPS and configured
   sample FPS are finite and positive, and timestamps are finite and strictly
   increasing. Its current code only checks ordering and can divide by zero or
   accept NaN timestamps. I have not guessed how its missing modules should behave.

Custom processors must be safe to retry: a process interrupted by deployment or
restart is requeued and may run again. Write only inside your job directory, do
not create non-idempotent external side effects, and keep temporary output within
the configured disk budget. Uploaded inputs use a fixed filename with no extension;
if your library requires an extension, adapt its input handling without duplicating
the whole file. The built-in FFmpeg processor probes file contents.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `PORT` | `8000` | Listener port; Railway supplies it |
| `STORAGE_DIR` | `/app/storage` | Mounted storage and SQLite database |
| `APP_USERNAME` | `admin` | Optional shared Basic username |
| `APP_PASSWORD` | unset | Set before public deployment |
| `ALLOWED_ORIGINS` | empty | Comma-separated exact external frontend origins |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Trusted proxy IPs; `*` only behind a trusted edge |
| `MAX_ACTIVE_JOBS` | `4` | Total uploading + queued + processing jobs; one runs at a time |
| `MAX_OUTPUT_BYTES` | `2147483648` | Reserved combined output/temp budget per active job |
| `MIN_FREE_BYTES` | `536870912` | Free-space safety margin |
| `JOB_TIMEOUT_SECONDS` | `3600` | Processor wall-clock limit |
| `UPLOAD_TTL_SECONDS` | `3600` | Remove abandoned uploads after this much inactivity |
| `RETENTION_SECONDS` | `86400` | Remove terminal jobs and their outputs after this duration |
| `DEMO_DELAY_SECONDS` | `10` | Demo-only simulated delay; use `0` for tests |

Hard limits: file size 2 GiB, chunk size 10 MiB, initialization body 4 KiB,
one processor, and Uvicorn concurrency 32. No Uvicorn keep-alive setting overrides
Railway's upload timeout. CORS is browser policy, not authentication or a firewall.

## Recovery, operations, and boundaries

- **Restart behavior:** acknowledged upload offsets and queued work survive with
  the volume. Extra uncommitted bytes are truncated on startup. Interrupted
  processors replay from the original input. Missing inputs produce a failed job.
  Without the volume, deployment replacement loses data and job IDs.
- **Retention:** every 60 seconds, remove abandoned uploads and expired terminal
  jobs. Active uploads and in-flight downloads are protected from cleanup. Inputs
  are removed after terminal status; results remain until retention expiry.
- **Admission:** reject excess jobs with 429 and insufficient disk with 507. File
  size/offset errors use 400/413/422. A declared size alone does not bypass actual
  streamed byte checks. Local filenames are fixed and UUID paths are validated.
- **Processing failures:** return failed status, remove incomplete outputs, and
  keep details in the service logs. The public error does not expose an FFmpeg
  command line or traceback. `/healthz` fails if the queue worker exits unexpectedly.
- **Resource controls:** FFmpeg uses bounded thread counts. Processing timeouts
  kill the child process group, including FFmpeg. The worker checks output budget
  and free disk once per second. This is a cooperative budget, not a hard filesystem
  quota; a custom processor may temporarily overshoot or allocate large amounts of
  RAM. Set Railway resource limits and test your custom code. User-supplied code
  must never be executed.
- **Resume:** reload restores status; to resume an interrupted upload, reselect
  the exact original file (name, length, and modification time must match). This
  is convenience matching, not a cryptographic identity check. Do not substitute
  another file's content mid-upload.
- **Downloads:** direct FileResponse streams from disk, supports byte ranges, and
  does not load the MP4 in RAM. Very slow multi-GB downloads remain subject to the
  host's overall request duration; a range-capable client can resume. At larger
  scale, serve outputs from object storage using signed URLs.
- **Availability:** one instance and an in-process dispatcher mean deploys can
  interrupt current work and cause downtime. For multiple replicas, per-user
  isolation, or stronger delivery guarantees, migrate to object storage,
  Postgres, a durable external queue, and real user authentication.

## Verify

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

The tests cover real FFmpeg output, background response timing, retries and byte
offsets, content limits, failed inputs, disk admission, CORS/authentication,
restart recovery, retention, ranged downloads, and child termination.

Optional real-size transfer (writes 2 GiB to temporary disk and removes it after):

```bash
python tests/large_upload_check.py
```

This uses a real Uvicorn HTTP server, uploads exactly 2,147,483,648 bytes in
10 MiB requests, checks both outputs, and reports the API process's peak resident
memory on Linux. The synthetic source is sparse, so the source fixture does not
consume another 2 GiB. This local test cannot establish Railway throughput or
your custom model's RAM requirements.

## Railway references

Deployment guidance checked against Railway's documentation on September 26, 2026:

- [Networking request limits](https://docs.railway.com/networking/public-networking/specs-and-limits)
- [Dockerfile deployment](https://docs.railway.com/builds/dockerfiles)
- [Docker start commands](https://docs.railway.com/deployments/start-command)
- [Persistent volumes and permissions](https://docs.railway.com/volumes)
- [Public domain setup](https://docs.railway.com/networking/public-networking)
- [Health checks](https://docs.railway.com/deployments/healthchecks)
