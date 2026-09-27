# RoadLens for Railway

This package contains the website and the model files it needs. The Python server
serves the page, receives uploads, and runs analysis in a background thread in the
same container. After deployment, processing runs on Railway, not your laptop.

## Deploy

1. Extract this ZIP and put its contents in the root of a GitHub repository.
   Keep `Dockerfile`, `requirements.txt`, and `railway.json` at the repository root.
2. In Railway, create a project and deploy that GitHub repository.
3. Railway uses the Dockerfile. The start command is `python website/server.py`.
   The server listens on `0.0.0.0` and reads Railway's `PORT` automatically.
4. Open service Settings > Networking > Public Networking > Generate Domain.
5. Open the generated HTTPS address and upload an MP4.

No external model API or secondary server is needed. Both model checkpoints are
included, and the Docker image installs CPU-only PyTorch and ffmpeg. If the
weights folder was not committed, `ensure_weights.py` downloads both checkpoints
during the image build and verifies their SHA256 hashes. Inference remains offline.

## Fixing the missing weights build error

Replace the repository files with the extracted contents of this updated ZIP,
including `Dockerfile`, `.dockerignore`, and `ensure_weights.py`, then commit and
push. Do not upload the ZIP itself as your application source.

Keep the Railway Root Directory set to the folder that contains the Dockerfile,
`requirements.txt`, `ensure_weights.py`, `website/`, `src/`, and `configs/`.
If these are in your repository root, use `/` as the Root Directory.

The build no longer has a `COPY weights ./weights` instruction. It copies the
available application files and obtains any missing model files before startup.
Missing `website/`, `src/`, or `configs/` still means the upload is incomplete.
When bundled weights are present and valid, no model download is required.

## Limits and operation

- Maximum file size: exactly 200,000,000 bytes (200 MB), enforced in the browser,
  job creation, and streamed upload. Some operating systems show this as 190.7 MiB.
- Existing limits retained: MP4 only, 10 minutes maximum, up to 4K resolution.
- One analysis at a time across all visitors. Keep one service replica because
  jobs and their temporary files belong to one running process.
- Uploads and results are temporary, expire after two hours from job creation,
  and disappear on container restart or redeploy. At most four recent jobs remain.
- CPU processing time and memory depend on duration, resolution and camera settings.
  Check Railway's memory usage during a real video run and allocate more if needed.
- The C3896 camera profile only fits its original camera view. For other cameras,
  use the uncalibrated profile and configure geometry before relying on event rules.

## Optional local Docker run

```sh
docker build -t roadlens .
docker run --rm -p 8080:8080 -e PORT=8080 roadlens
```

Open http://localhost:8080. Health endpoint: `/health`.

## Validation

The web/API checks cover upload, status polling, result download, video byte ranges,
size rejection, and the exact 200 MB boundary. Model preparation was checked with bundled files and a simulated missing-folder
download, including checksum rejection. Docker image build and real model
inference have not been verified in the preparation environment.

Railway references:
- https://docs.railway.com/builds/dockerfiles
- https://docs.railway.com/deployments/healthchecks
- https://docs.railway.com/networking/public-networking

## Team and upload popup

JAM members and profile links are in `configs/team.json`. Each `photo` field is
empty. To add a photo, put it in `website/public/photos/` and set the field to
`/photos/your-file.jpg`. Empty fields display blank photo placeholders.

Oversized file selections and HTTP 413 responses open a dialog linking to
https://github.com/CosmosByME/traffic-event-cv for local installation. The external
repository may have its own limits; local processing is not guaranteed unlimited.

The second camera profile is labeled Nighttime framing. Its configuration remains
uncalibrated, so event rules stay disabled until geometry is configured. No new
night-trained model or geometry is introduced.
