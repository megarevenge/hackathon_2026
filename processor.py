"""Replace process_video's body to integrate your own synchronous Python pipeline."""
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time


def process_video(input_path: str, output_json_path: str, output_mp4_path: str):
    """Working demo: inspect the input and encode its first 10 seconds to H.264 MP4.

    CUSTOM LOGIC GOES HERE. Keep this three-path signature and create BOTH files.
    Runs in a separate Python process so CPU work never blocks FastAPI. Raise an
    exception on failure. Jobs can replay after a restart: avoid external side effects.
    The server gives input_path no extension; detect the format from file contents.

    The supplied reference/solution.py cannot run without its src package and models.
    See README before wiring detect_events into this function.
    """
    source = Path(input_path)
    # Explicit MOV/MP4 demuxer + no external data references/network protocols.
    safe_input = ["-protocol_whitelist", "file,pipe", "-format_whitelist", "mov",
                  "-f", "mov", "-enable_drefs", "0"]
    probe = subprocess.run(
        ["ffprobe", "-v", "error", *safe_input, "-show_entries",
         "format=duration:stream=codec_type,codec_name,width,height", "-of", "json", str(source)],
        check=True, capture_output=True, text=True, timeout=30,
    )
    info = json.loads(probe.stdout)
    videos = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    if not videos:
        raise ValueError("The uploaded file contains no video stream")
    duration = float(info.get("format", {}).get("duration", 0))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("The input does not have a valid duration")
    time.sleep(max(0, float(os.getenv("DEMO_DELAY_SECONDS", "10"))))

    # Demo deliberately returns a short preview, NOT an analyzed/annotated full video.
    # Replace/remove -t and this encoder when integrating your full-video algorithm.
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
         "-threads", "2", *safe_input, "-i", str(source), "-t", "10",
         "-map", "0:v:0", "-map", "0:a:0?", "-map_metadata", "-1",
         "-vf", "scale=w='min(1280,trunc(iw/2)*2)':h=-2,format=yuv420p",
         "-filter_threads", "1", "-c:v", "libx264", "-threads", "2",
         "-preset", "veryfast", "-crf", "26", "-c:a", "aac", "-b:a", "128k",
         "-movflags", "+faststart", "-f", "mp4", output_mp4_path],
        check=True,
    )
    result = {"mode": "demo", "message": "Dummy metadata; no event detection has been run",
              "input_bytes": source.stat().st_size, "input_duration_seconds": duration,
              "preview_duration_seconds": min(duration, 10), "video": videos[0],
              "events": [], "output_video": "processed_video.mp4"}
    Path(output_json_path).write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("Usage: processor.py INPUT OUTPUT_JSON OUTPUT_MP4")
    process_video(*sys.argv[1:])
