"""Opt-in real 2 GiB transfer over TCP; requires about 2.1 GiB free disk.

Run: python tests/large_upload_check.py
Uses 10 MiB HTTP requests and a sparse, valid MP4 with a large trailing free box.
Only the small fixture and one chunk are ever held in client RAM.
"""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
SIZE = 2 * 1024**3
CHUNK = 10 * 1024**2


def run():
    with tempfile.TemporaryDirectory(prefix="video-large-check-") as directory:
        folder = Path(directory)
        media = folder / "large.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x64:r=5",
                        "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(media)], check=True)
        free_box_size = SIZE - media.stat().st_size
        with media.open("ab") as file:
            file.write(struct.pack(">I4s", free_box_size, b"free"))
            file.truncate(SIZE)
        with socket.socket() as port_socket:
            port_socket.bind(("127.0.0.1", 0))
            port = port_socket.getsockname()[1]
        env = {**os.environ, "PORT": str(port), "STORAGE_DIR": str(folder / "storage"),
               "MIN_FREE_BYTES": "0", "MAX_OUTPUT_BYTES": str(64 * 1024**2),
               "DEMO_DELAY_SECONDS": "0", "APP_PASSWORD": ""}
        runner = (
            "import os, resource, uvicorn, main\n"
            "@main.app.get('/_test/memory')\n"
            "async def memory():\n"
            "    return {'peak': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}\n"
            "uvicorn.run(main.app, host='127.0.0.1', port=int(os.environ['PORT']), log_level='warning')\n"
        )
        with (folder / "server.log").open("w+") as log:
            server = subprocess.Popen([sys.executable, "-c", runner], cwd=ROOT, env=env, stdout=log, stderr=log)
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=60, trust_env=False) as client:
                    for _ in range(100):
                        try:
                            if client.get("/healthz").status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        time.sleep(.1)
                    else:
                        raise RuntimeError("Server did not start")
                    response = client.post("/api/uploads", json={"filename": "large.mp4", "size": SIZE})
                    response.raise_for_status()
                    job = response.json()["job_id"]
                    offset, start = 0, time.monotonic()
                    with media.open("rb") as file:
                        while data := file.read(CHUNK):
                            response = client.post(f"/api/upload?job_id={job}&offset={offset}", content=data,
                                                   headers={"Content-Type": "application/octet-stream"})
                            response.raise_for_status()
                            offset = response.json()["received"]
                    upload_seconds = round(time.monotonic() - start, 2)
                    assert offset == SIZE
                    for _ in range(100):
                        state = client.get(f"/api/status/{job}").json()
                        if state["status"] in ("completed", "failed"):
                            break
                        time.sleep(.1)
                    assert state["status"] == "completed", state
                    assert client.get(f"/api/download/{job}/json").json()["input_bytes"] == SIZE
                    assert client.get(f"/api/download/{job}/mp4").status_code == 200
                    usage = client.get('/_test/memory').json()['peak']
                    peak_mib = usage / (1024**2 if sys.platform == "darwin" else 1024)
                    assert peak_mib < 256, peak_mib
                    report = {"uploaded_bytes":offset,"upload_seconds":upload_seconds,"status":state["status"],
                              "server_peak_rss_mib":round(peak_mib, 2)}
            except BaseException:
                log.flush()
                print((folder / "server.log").read_text()[-5000:], file=sys.stderr)
                raise
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    run()
