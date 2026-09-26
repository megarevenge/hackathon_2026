"""Single-instance video service. Run with exactly one Uvicorn worker."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
import shutil
import signal
import sqlite3
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import aiofiles
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field
from starlette.requests import ClientDisconnect

BASE = Path(__file__).resolve().parent
STORAGE = Path(os.getenv("STORAGE_DIR", "/app/storage")).resolve()
MAX_UPLOAD = 2 * 1024**3  # 2 GiB = 2,147,483,648 bytes; includes decimal 2 GB.
CHUNK_SIZE = 10 * 1024**2
MAX_ACTIVE = int(os.getenv("MAX_ACTIVE_JOBS", "4"))
MAX_OUTPUT = int(os.getenv("MAX_OUTPUT_BYTES", str(2 * 1024**3)))
MIN_FREE = int(os.getenv("MIN_FREE_BYTES", str(512 * 1024**2)))
JOB_TIMEOUT = int(os.getenv("JOB_TIMEOUT_SECONDS", "3600"))
UPLOAD_TTL = int(os.getenv("UPLOAD_TTL_SECONDS", "3600"))
RETENTION = int(os.getenv("RETENTION_SECONDS", "86400"))
PASSWORD = os.getenv("APP_PASSWORD", "")
USERNAME = os.getenv("APP_USERNAME", "admin")
ORIGINS = [x.strip().rstrip("/") for x in os.getenv("ALLOWED_ORIGINS", "").split(",") if x.strip()]
if "*" in ORIGINS:
    raise RuntimeError("ALLOWED_ORIGINS must contain explicit origins, not '*'.")
if min(MAX_ACTIVE, MAX_OUTPUT, JOB_TIMEOUT, UPLOAD_TTL, RETENTION) < 1 or MIN_FREE < 0:
    raise RuntimeError("Invalid resource limit configuration.")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("video-worker")
basic = HTTPBasic(auto_error=False)


async def authenticate(credentials: HTTPBasicCredentials | None = Depends(basic)) -> None:
    if not PASSWORD:
        return
    valid = credentials is not None
    if valid:
        valid = secrets.compare_digest(credentials.username.encode(), USERNAME.encode())
        valid &= secrets.compare_digest(credentials.password.encode(), PASSWORD.encode())
    if not valid:
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": 'Basic realm="Video Studio"'})


def _db(sql: str, args: tuple = (), fetch: bool = False):
    with sqlite3.connect(STORAGE / "jobs.sqlite3", timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(sql, args)
        return [dict(row) for row in cursor.fetchall()] if fetch else None


async def db(sql: str, args: tuple = (), fetch: bool = False):
    return await asyncio.to_thread(_db, sql, args, fetch)


async def get_job(job_id: UUID) -> dict:
    rows = await db("SELECT * FROM jobs WHERE id = ?", (str(job_id),), True)
    if not rows:
        raise HTTPException(404, "Job not found or expired")
    return rows[0]


def job_dir(job_id: str | UUID) -> Path:
    return STORAGE / str(UUID(str(job_id)))


async def mark(job_id: str, state: str, error: str | None = None) -> None:
    await db("UPDATE jobs SET state=?, error=?, updated=? WHERE id=?", (state, error, time.time(), job_id))


async def terminate(proc: asyncio.subprocess.Process) -> None:
    # The processor and FFmpeg share a process group, including custom children.
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
    await proc.wait()


async def run_processor(job: dict) -> None:
    folder = job_dir(job["id"])
    for name in ("result.json", "processed_video.mp4"):
        (folder / name).unlink(missing_ok=True)
    command = [sys.executable, str(BASE / "processor.py"), str(folder / "input"),
               str(folder / "result.json"), str(folder / "processed_video.mp4")]
    # No captured pipes: large processor logs cannot accumulate in API RAM.
    proc = await asyncio.create_subprocess_exec(*command, start_new_session=True)
    started = time.monotonic()
    try:
        while proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=1)
            except TimeoutError:
                pass
            if time.monotonic() - started > JOB_TIMEOUT:
                raise RuntimeError("Processing exceeded the configured time limit")
            output_size = sum(p.stat().st_size for p in folder.iterdir() if p.name != "input")
            if output_size > MAX_OUTPUT:
                raise RuntimeError("Processing exceeded the configured output size limit")
            if shutil.disk_usage(STORAGE).free < MIN_FREE:
                raise RuntimeError("Insufficient disk space during processing")
        if proc.returncode:
            raise RuntimeError("Video processing failed; check the service logs for this job")
        # Validate the contract without reading an arbitrarily large result into memory.
        for name in ("result.json", "processed_video.mp4"):
            if not (folder / name).is_file() or (folder / name).stat().st_size == 0:
                raise RuntimeError("The processor did not create both output files")
    finally:
        await terminate(proc)


async def worker(app: FastAPI) -> None:
    while True:
        job_id = await app.state.queue.get()
        try:
            job = await get_job(UUID(job_id))
            if job["state"] != "pending":
                continue
            await mark(job_id, "processing")
            log.info("Processing job %s", job_id)
            try:
                await run_processor(job)
            except asyncio.CancelledError:
                # Safe to replay: the processor must only write inside this job directory.
                await mark(job_id, "pending")
                raise
            except Exception as exc:
                log.exception("Job %s failed", job_id)
                await mark(job_id, "failed", str(exc) if isinstance(exc, RuntimeError) else "Processing failed; see service logs")
                for name in ("result.json", "processed_video.mp4"):
                    (job_dir(job_id) / name).unlink(missing_ok=True)
            else:
                await mark(job_id, "completed")
                log.info("Completed job %s", job_id)
            # Input is no longer needed after terminal state is committed.
            (job_dir(job_id) / "input").unlink(missing_ok=True)
        finally:
            app.state.queue.task_done()


async def cleanup_once(app: FastAPI) -> None:
    async with app.state.admission:
        now = time.time()
        rows = await db("SELECT id FROM jobs WHERE (state='uploading' AND updated < ?) OR "
                        "(state IN ('completed','failed') AND updated < ?)",
                        (now - UPLOAD_TTL, now - RETENTION), True)
        for row in rows:
            job_id = row["id"]
            if job_id in app.state.writing or app.state.downloads.get(job_id, 0):
                continue
            await asyncio.to_thread(shutil.rmtree, job_dir(job_id), True)
            await db("DELETE FROM jobs WHERE id=?", (job_id,))


async def janitor(app: FastAPI) -> None:
    while True:
        await asyncio.sleep(60)
        try:
            await cleanup_once(app)
        except Exception:
            log.exception("Retention cleanup failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    STORAGE.mkdir(parents=True, exist_ok=True)
    # Linux/macOS advisory lock prevents accidental multi-worker corruption.
    import fcntl
    lock = (STORAGE / ".instance.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        raise RuntimeError("Storage already in use. Run exactly one replica and one Uvicorn worker.")
    tasks = []
    try:
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise RuntimeError("FFmpeg and ffprobe must be installed")
        await db("PRAGMA journal_mode=WAL", fetch=True)
        await db("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, filename TEXT NOT NULL, "
                 "size INTEGER NOT NULL, received INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL, "
                 "error TEXT, created REAL NOT NULL, updated REAL NOT NULL)")
        app.state.queue = asyncio.Queue(maxsize=MAX_ACTIVE)
        app.state.admission = asyncio.Lock()
        app.state.writing = set()
        app.state.downloads = {}
        # Reconcile crashes between a disk write and its metadata commit.
        rows = await db("SELECT * FROM jobs", fetch=True)
        active = []
        for job in rows:
            path = job_dir(job["id"]) / "input"
            if job["state"] == "uploading":
                actual = path.stat().st_size if path.exists() else -1
                if actual < job["received"]:
                    await mark(job["id"], "failed", "Upload data is missing; upload the file again")
                elif actual > job["received"]:
                    with path.open("r+b") as file:
                        file.truncate(job["received"])
            elif job["state"] in ("pending", "processing"):
                if not path.exists() or path.stat().st_size != job["size"]:
                    await mark(job["id"], "failed", "Input file is missing; upload the file again")
                else:
                    await mark(job["id"], "pending")
                    active.append(job["id"])
            else:
                path.unlink(missing_ok=True)
        # Existing queues may be larger after an operator lowers MAX_ACTIVE_JOBS.
        app.state.queue = asyncio.Queue(maxsize=max(MAX_ACTIVE, len(active)))
        for job_id in active:
            app.state.queue.put_nowait(job_id)
        await cleanup_once(app)
        if not PASSWORD:
            log.warning("APP_PASSWORD is unset: API is public. Set it before public deployment.")
        tasks = [asyncio.create_task(worker(app)), asyncio.create_task(janitor(app))]
        app.state.worker = tasks[0]
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        lock.close()


app = FastAPI(title="Video Studio", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


class BodyLimit:
    """ASGI-level streamed body cap, including requests without Content-Length."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        limit = MAX_UPLOAD if scope["path"] == "/api/upload" else 4096
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse({"detail": "Invalid Content-Length"}, 400)(scope, receive, send)
        if declared < 0 or declared > limit:
            return await JSONResponse({"detail": "Request body exceeds the upload limit"}, 413)(scope, receive, send)
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise HTTPException(413, "Request body exceeds the upload limit")
            return message

        async def secure_send(message):
            if message["type"] == "http.response.start":
                message["headers"] += [(b"x-content-type-options", b"nosniff"),
                                       (b"referrer-policy", b"no-referrer"),
                                       (b"cache-control", b"no-store")]
            await send(message)
        await self.app(scope, limited_receive, secure_send)


app.add_middleware(BodyLimit)
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_credentials=True,
                   allow_methods=["GET", "POST", "OPTIONS"],
                   allow_headers=["Authorization", "Content-Type", "Range"],
                   expose_headers=["Content-Disposition", "Content-Length", "Content-Range"], max_age=600)


@app.get("/healthz")
async def health():
    if app.state.worker.done():
        raise HTTPException(503, "Background worker unavailable")
    return {"status": "ok"}


@app.get("/", dependencies=[Depends(authenticate)])
async def index():
    return FileResponse(BASE / "static" / "index.html", media_type="text/html",
                        headers={"Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                                 "style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"})


@app.get("/api/config", dependencies=[Depends(authenticate)])
async def config():
    return {"max_upload_bytes": MAX_UPLOAD, "chunk_bytes": CHUNK_SIZE, "retention_seconds": RETENTION}


class UploadSpec(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0, le=MAX_UPLOAD, strict=True)


async def create_job(spec: UploadSpec) -> dict:
    if Path(spec.filename).suffix.lower() not in (".mp4", ".mov"):
        raise HTTPException(415, "Only .mp4 and .mov files are accepted")
    if any(c in spec.filename for c in ("/", "\\", "\0")):
        raise HTTPException(400, "Filename must not contain a path")
    async with app.state.admission:
        rows = await db("SELECT size, received, state FROM jobs WHERE state IN ('uploading','pending','processing')", fetch=True)
        if len(rows) >= MAX_ACTIVE:
            raise HTTPException(429, "All job slots are busy; retry after another job finishes", headers={"Retry-After": "30"})
        # Reserve future input + output for ALL admitted jobs, not just this upload.
        reserved = sum(row["size"] - row["received"] + MAX_OUTPUT for row in rows)
        if shutil.disk_usage(STORAGE).free < MIN_FREE + reserved + spec.size + MAX_OUTPUT:
            raise HTTPException(507, "Not enough disk space to accept this video")
        job_id, now = str(uuid4()), time.time()
        folder = job_dir(job_id)
        folder.mkdir(mode=0o700)
        (folder / "input").touch(mode=0o600)
        try:
            await db("INSERT INTO jobs(id,filename,size,state,created,updated) VALUES (?,?,?,'uploading',?,?)",
                     (job_id, spec.filename, spec.size, now, now))
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
    return await get_job(UUID(job_id))


@app.post("/api/uploads", status_code=201, dependencies=[Depends(authenticate)])
async def initialize_upload(spec: UploadSpec):
    """Optional preflight: reserve capacity and return job_id before any video bytes."""
    job = await create_job(spec)
    return {"job_id": job["id"], "received": 0, "chunk_bytes": CHUNK_SIZE}


@app.post("/api/upload", status_code=202, dependencies=[Depends(authenticate)])
async def upload(request: Request, job_id: UUID | None = None, offset: int = Query(0, ge=0),
                 filename: str | None = Query(None, min_length=1, max_length=255),
                 total_size: int | None = Query(None, gt=0, le=MAX_UPLOAD)):
    """Raw binary body. Existing job: <=10 MiB/request. New job: one-shot <=2 GiB."""
    if request.headers.get("content-type", "").split(";")[0].lower() != "application/octet-stream":
        raise HTTPException(415, "Send raw bytes with Content-Type: application/octet-stream")
    if request.headers.get("content-encoding", "identity") != "identity":
        raise HTTPException(415, "Compressed HTTP request bodies are not supported")
    new_job = job_id is None
    if new_job:
        if filename is None or total_size is None or offset != 0:
            raise HTTPException(400, "A new upload requires filename, total_size and offset=0")
        job = await create_job(UploadSpec(filename=filename, size=total_size))
        job_id = UUID(job["id"])
    key = str(job_id)
    # Protect against concurrent chunks and retention cleanup without holding a global
    # lock during an entire network upload.
    async with app.state.admission:
        if key in app.state.writing:
            raise HTTPException(409, "Another chunk is being written; query status and retry")
        app.state.writing.add(key)
    try:
        job = await get_job(job_id)
        if job["state"] != "uploading" or offset != job["received"]:
            raise HTTPException(409, "Offset or state changed; query status before retrying")
        limit = min(MAX_UPLOAD if new_job else CHUNK_SIZE, job["size"] - offset)
        length = request.headers.get("content-length")
        if length and int(length) > limit:
            raise HTTPException(413, "Chunk exceeds the remaining file size or chunk limit")
        path = job_dir(job_id) / "input"
        count = 0
        committed = False
        try:
            async with aiofiles.open(path, "r+b") as file:
                await file.seek(offset)
                buffer = bytearray()
                async for incoming in request.stream():
                    count += len(incoming)
                    if count > limit:
                        raise HTTPException(413, "Upload exceeds the declared size or chunk limit")
                    # ASGI transport chunks have arbitrary sizes. Coalesce writes into
                    # 10 MiB buffers; never read/spool a whole video through UploadFile.
                    view = memoryview(incoming)
                    while view:
                        take = min(CHUNK_SIZE - len(buffer), len(view))
                        buffer.extend(view[:take])
                        view = view[take:]
                        if len(buffer) == CHUNK_SIZE:
                            await file.write(buffer)
                            buffer.clear()
                if not count:
                    raise HTTPException(400, "Empty upload chunk")
                if new_job and count != job["size"]:
                    raise HTTPException(400, "One-shot body length does not match total_size")
                if buffer:
                    await file.write(buffer)
                await file.flush()
                await asyncio.to_thread(os.fsync, file.fileno())
            received = offset + count
            state = "pending" if received == job["size"] else "uploading"
            await db("UPDATE jobs SET received=?,state=?,updated=? WHERE id=?", (received, state, time.time(), key))
            committed = True
            if state == "pending":
                app.state.queue.put_nowait(key)
            return {"job_id": key, "received": received, "queued": state == "pending"}
        except ClientDisconnect:
            raise HTTPException(400, "Upload disconnected; retry from the last committed offset")
        except OSError:
            log.exception("Disk write failed for %s", key)
            raise HTTPException(507, "Could not write upload to storage")
        finally:
            if not committed:
                # Discard a partial request. Previously acknowledged chunks survive.
                async with aiofiles.open(path, "r+b") as file:
                    await file.truncate(offset)
                if new_job:
                    await mark(key, "failed", "Upload did not complete; upload the file again")
    except BaseException:
        if new_job:
            current = await get_job(job_id)
            if current["state"] == "uploading":
                await mark(key, "failed", "Upload did not complete; upload the file again")
            if current["state"] in ("uploading", "failed"):
                (job_dir(job_id) / "input").unlink(missing_ok=True)
        raise
    finally:
        app.state.writing.discard(key)


@app.get("/api/status/{job_id}", dependencies=[Depends(authenticate)])
async def status(job_id: UUID):
    job = await get_job(job_id)
    state = job["state"]
    return {"status": "pending" if state == "uploading" else state,
            "progress": 100 if state == "completed" else 0,
            "phase": "uploading" if state == "uploading" else state,
            "received": job["received"], "size": job["size"], "error": job["error"],
            "expires_at": job["updated"] + RETENTION if state in ("completed", "failed") else None}


class PinnedFileResponse(FileResponse):
    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            app.state.downloads[self.job_key] -= 1
            if not app.state.downloads[self.job_key]:
                del app.state.downloads[self.job_key]


@app.get("/api/download/{job_id}/{file_type}", dependencies=[Depends(authenticate)])
async def download(job_id: UUID, file_type: Literal["json", "mp4"]):
    async with app.state.admission:
        job = await get_job(job_id)
        if job["state"] != "completed":
            raise HTTPException(409, "Outputs are not ready")
        name, media = ("result.json", "application/json") if file_type == "json" else ("processed_video.mp4", "video/mp4")
        path = job_dir(job_id) / name
        if not path.is_file():
            raise HTTPException(404, "Output file is missing")
        response = PinnedFileResponse(path, filename=name, media_type=media)
        response.job_key = str(job_id)
        app.state.downloads[str(job_id)] = app.state.downloads.get(str(job_id), 0) + 1
        return response


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), workers=1,
                proxy_headers=True, forwarded_allow_ips=os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1"),
                timeout_keep_alive=5, timeout_graceful_shutdown=30, limit_concurrency=32)
