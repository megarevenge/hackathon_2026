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
included, and the Docker image installs CPU-only PyTorch and ffmpeg.

## Limits and operation

- Maximum file size: exactly 50,000,000 bytes (50 MB), enforced in the browser,
  job creation, and streamed upload. Some operating systems show this as 47.7 MiB.
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
size rejection, and the exact 50 MB boundary. Docker image build and real model
inference have not been verified in the preparation environment.

Railway references:
- https://docs.railway.com/builds/dockerfiles
- https://docs.railway.com/deployments/healthchecks
- https://docs.railway.com/networking/public-networking
