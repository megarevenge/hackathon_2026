# Validation record

Checked September 26, 2026, on Linux with Python 3.12.14 and system FFmpeg.

- `python -m pytest -q`: **10 passed**.
- Real Uvicorn TCP upload: **2,147,483,648 bytes**, sent as sequential 10 MiB
  requests, accepted and processed successfully; JSON and MP4 downloaded.
- API process peak resident memory: **63.67 MiB**, measured using
  `resource.getrusage(RUSAGE_SELF)` inside the running API process. This excludes
  the separate FFmpeg/processor process and kernel filesystem cache.
- The full-size fixture was a valid short MP4 with a large trailing free box.
  It tests the entire 2 GiB transfer and disk-writing path, not the CPU/memory
  requirements of decoding a 2 GiB long/high-resolution source or a custom model.
- JavaScript syntax compilation, Python compilation, and `sh -n start.sh` passed.
- Test coverage includes valid and invalid media, background response timing,
  offset conflicts, upload rollback, size limits, authentication, CORS, capacity
  admission, persistent restart recovery, retention, byte-range downloads, and
  killing timed-out processing.

Limitations of this validation:

- Docker was unavailable, so the image was not built or run here.
- No Railway account deployment was performed; test the deployed service with
  your plan, volume, proxy, and expected traffic before use.
- Browser installation failed in the test environment. JavaScript syntax was
  checked, but a real-browser upload/visual test was not completed.
- Starlette's test client emitted a deprecation warning for its httpx adapter;
  the tests passed. This does not affect the production Uvicorn server.
- Your reference `solution.py` cannot be executed without its missing package,
  model, and configuration dependencies. No event-detection accuracy is claimed.
