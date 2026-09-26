# RoadLens HTML website

Independent frontend in `public/index.html`, `public/styles.css`, and `public/app.js`.
The Python API in `server.py` runs the existing model from `../src/`. No Node.js, npm or frontend build is needed. Opening index.html alone shows the design but cannot perform inference; run the server for a working demo.

From the project root:

```bash
.venv/bin/python website/server.py
```

Visit http://127.0.0.1:8080.

On a new machine, install shared dependencies with `python -m pip install -r requirements.txt` and obtain weights with `python scripts/download_weights.py`. The dependency list and Dockerfile both live at the project root.

## Uploads and results

MP4 up to 3 GB (3072 MB), 10 minutes, and 4K. Upload bodies are streamed to temporary disk files, not retained as a single multi-gigabyte Python object. Browser previews use object URLs. Files must still fit available temporary disk space; annotated export requires additional space. Only one upload/analysis runs at a time because model inference shares CPU/GPU resources.

Video decoding/model processing happens in a background worker so the page can poll progress. Original video and JSON artifacts are served from the same API; video range requests support seeking. The browser draws observed boxes in green and short predicted boxes in amber. Predicted boxes are display-only. A browser that cannot decode the original codec shows a message; JSON analysis can still finish. Use H.264 MP4 for the widest browser compatibility.

Configuration edits apply only to that job. Download the configuration to save it; the website does not silently overwrite configs/camera.json. Default profile is uncalibrated. C3896.draft.json belongs only to the C3896 camera and needs review. Team members and links come from configs/team.json.

Downloads: event/risk predictions JSON, full analysis JSON, labeled risk CSV, and optional annotated MP4. Results and uploads are temporary: retained for two hours, up to four completed jobs, or until server shutdown. Download anything you need to keep. Page refresh resumes the most recent job in the same browser session. Restarting the server clears job state.

## API

- `GET /api/info`: model readiness, limits, profiles and team.
- `POST /api/jobs`: JSON `{name, size, config, render}` → job ID.
- `PUT /api/jobs/{id}/video`: raw MP4 body with matching Content-Length.
- `GET /api/jobs/{id}`: state/progress/error.
- `GET /api/jobs/{id}/files/analysis.json`: complete result once done.
- Other files: `input.mp4`, `scene.jpg`, `predictions.json`, `risk.csv`, optional `annotated.mp4`.
- `DELETE /api/jobs/{id}`: remove an inactive temporary job.

Default binding is localhost. Public deployment is not included; for a hosted deployment run behind HTTPS with authentication/rate limits and an upload-aware reverse proxy. The upload endpoint expects Content-Length, and the proxy must support the intended body size. A static-only host cannot run the Python model.

The root-level solution.py and organizer interface are unchanged.

Docker (run these from the parent project, after obtaining weights):

```bash
docker build -t roadlens-web .
docker run --rm -p 127.0.0.1:8080:8080 roadlens-web
```

Local verification: five HTTP tests cover admission limits, invalid files/configuration, upload length, background completion, downloads and byte-range video requests. A real four-second road clip was uploaded through the browser and analyzed with YOLO, and clicking its event successfully sought to the event timestamp. A full 3 GB transfer and Docker build have not been exercised.
