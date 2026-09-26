"""Serve the HTML frontend and stream video uploads to disk. No build step."""
import argparse
import json
import math
import shutil
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import tornado.ioloop
import tornado.web
from src.config import load_config, validate_config

MAX_BYTES = 3 * 1024**3
MAX_SECONDS = 600
PROFILES = {'camera.json': 'New camera · configure before detecting events',
            'C3896.draft.json': 'C3896 intersection · draft calibration'}


class Jobs:
    def __init__(self, processor=None):
        self.items = {}
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.processor = processor or self.process

    def create(self, name, size, config, render):
        with self.lock:
            self.prune()
            if any(j['state'] in ('waiting','uploading','processing','rendering') for j in self.items.values()):
                raise tornado.web.HTTPError(409, reason='Another video is active. Please wait for it to finish.')
            if len(self.items) >= 4:
                oldest = min(self.items, key=lambda k: self.items[k]['created'])
                self.remove(oldest)
            token = uuid.uuid4().hex
            temp = tempfile.TemporaryDirectory(prefix='roadlens-web-')
            self.items[token] = dict(id=token, name=name, size=size, config=config, render=render,
                                     state='waiting', progress=0, created=time.time(), temp=temp, warning=None)
            return self.items[token]

    def get(self, token):
        with self.lock:
            job = self.items.get(token)
            if job is None:
                raise tornado.web.HTTPError(404, reason='This analysis has expired. Upload the video again.')
            return job

    def update(self, token, **changes):
        with self.lock:
            if token in self.items:
                self.items[token].update(changes)

    def public(self, token):
        with self.lock:
            job = self.get(token)
            return {k:job[k] for k in ('id','name','state','progress','warning') if k in job} | {'error':job.get('error')}

    def remove(self, token):
        with self.lock:
            job = self.items.pop(token, None)
            if job:
                job['temp'].cleanup()

    def prune(self):
        with self.lock:
            for token,job in list(self.items.items()):
                expiry = 300 if job['state']=='waiting' else 7200
                if job['state'] in ('done','failed','waiting') and time.time()-job['created'] > expiry:
                    self.remove(token)

    def submit(self, token):
        self.update(token,state='processing',progress=0)
        self.executor.submit(self.run,token)

    def run(self, token):
        try:
            self.processor(token)
        except Exception as exc:
            self.update(token,state='failed',error=str(exc),progress=0)

    def process(self, token):
        import cv2
        from src.pipeline import analyze
        from src.output import validate_prediction
        from src.render import render_video, draw_scene
        job = self.get(token)
        folder = Path(job['temp'].name)
        path = folder/'input.mp4'
        cap = cv2.VideoCapture(str(path))
        try:
            fps,n = cap.get(cv2.CAP_PROP_FPS),cap.get(cv2.CAP_PROP_FRAME_COUNT)
            ok,frame = cap.read()
            if not ok or not math.isfinite(fps) or fps<=0 or not math.isfinite(n) or n<=0:
                raise ValueError('This MP4 could not be decoded. Try another video or export it as H.264 MP4.')
            if n/fps > MAX_SECONDS:
                raise ValueError('Maximum video length is 10 minutes.')
            if frame.shape[0]*frame.shape[1] > 3840*2160:
                raise ValueError('Maximum supported resolution is 4K.')
            preview = cv2.resize(frame,(1280,round(frame.shape[0]*1280/frame.shape[1])))
            cv2.imwrite(str(folder/'scene.jpg'),draw_scene(preview,job['config']))
        finally:
            cap.release()
        data = analyze(path,job['config'],lambda p:self.update(token,progress=round(p*100)))
        validate_prediction(data['events'],data['risk'],data['meta']['duration'])
        predictions = {k:data[k] for k in ('events','risk')}
        (folder/'predictions.json').write_text(json.dumps(predictions,allow_nan=False))
        with (folder/'risk.csv').open('w') as out:
            out.write('timestamp_seconds,score_0_to_1\n')
            for timestamp,score in data['risk']:
                out.write(f'{timestamp},{score}\n')
        export_ready = False
        if job['render']:
            self.update(token,state='rendering',progress=100)
            try:
                render_video(path,data,folder/'annotated.mp4')
                export_ready = True
                if not shutil.which('ffmpeg'):
                    self.update(token,warning='Annotated MP4 is available for download. Install ffmpeg for browser-compatible H.264.')
            except Exception as exc:
                self.update(token,warning=f'Analysis succeeded, but annotated export failed: {exc}')
        data['website_export_available'] = export_ready
        (folder/'analysis.json').write_text(json.dumps(data,allow_nan=False))
        self.update(token,state='done',progress=100)

    def close(self):
        self.executor.shutdown(wait=True)
        for token in list(self.items):
            self.remove(token)


class API(tornado.web.RequestHandler):
    @property
    def jobs(self):
        return self.settings['jobs']

    def set_default_headers(self):
        self.set_header('Cache-Control','no-store')
        self.set_header('X-Content-Type-Options','nosniff')

    def prepare(self):
        origin = self.request.headers.get('Origin')
        if origin and urlparse(origin).netloc != self.request.host:
            raise tornado.web.HTTPError(403,reason='Cross-origin requests are not allowed.')

    def write_error(self, status_code, **kwargs):
        self.finish({'error':self._reason})


class Info(API):
    def get(self):
        team = json.loads((ROOT/'configs/team.json').read_text())
        self.write(dict(max_bytes=MAX_BYTES,max_seconds=MAX_SECONDS,profiles=[
            dict(id=name,label=label,config=load_config(ROOT/'configs'/name)) for name,label in PROFILES.items()],
            team=team,weights_ready=(ROOT/'weights/yolo11n.pt').is_file(),
            browser_export=bool(shutil.which('ffmpeg'))))


class CreateJob(API):
    def post(self):
        try:
            payload = json.loads(self.request.body)
            name = Path(payload['name']).name
            size = payload['size']
            if not name.lower().endswith('.mp4') or not isinstance(size,int) or not 0<size<=MAX_BYTES:
                raise ValueError('Choose an MP4 file up to 3 GB (3072 MB).')
            config = validate_config(payload['config'])
        except (ValueError,KeyError,TypeError,AttributeError,OverflowError) as exc:
            raise tornado.web.HTTPError(400,reason=f'Invalid upload settings: {exc}')
        job = self.jobs.create(name,size,config,bool(payload.get('render',False)))
        self.set_status(201)
        self.write(self.jobs.public(job['id']))


@tornado.web.stream_request_body
class Upload(API):
    def initialize(self):
        self.destination = None
        self.token = None
        self.submitted = False
        self.received = 0

    def prepare(self):
        super().prepare()
        token = self.path_args[0]
        if isinstance(token,bytes): token=token.decode()
        job = self.jobs.get(token)
        if job['state'] != 'waiting':
            raise tornado.web.HTTPError(409,reason='This upload is already in progress or finished.')
        try:
            length = int(self.request.headers.get('Content-Length','0'))
        except ValueError:
            length = 0
        if length != job['size'] or length>MAX_BYTES:
            self.jobs.remove(token)
            raise tornado.web.HTTPError(413,reason='Upload size does not match the selected file or exceeds 3 GB.')
        self.token = token
        self.expected = length
        self.destination = (Path(job['temp'].name)/'input.mp4').open('wb')
        self.jobs.update(token,state='uploading')

    def data_received(self,chunk):
        self.received += len(chunk)
        if self.received>self.expected:
            raise tornado.web.HTTPError(413,reason='Upload exceeds declared size.')
        self.destination.write(chunk)

    async def put(self,token):
        self.destination.close()
        if self.received != self.expected:
            self.jobs.remove(token)
            raise tornado.web.HTTPError(400,reason='Upload was incomplete.')
        self.submitted = True
        self.jobs.submit(token)
        self.set_status(202)
        self.write(self.jobs.public(token))

    def on_connection_close(self):
        if self.destination and not self.destination.closed:
            self.destination.close()
        if self.token and not self.submitted:
            self.jobs.remove(self.token)


class Job(API):
    def get(self,token):
        self.write(self.jobs.public(token))

    def delete(self,token):
        job = self.jobs.get(token)
        if job['state'] in ('processing','rendering','uploading'):
            raise tornado.web.HTTPError(409,reason='Wait until processing finishes before deleting this result.')
        self.jobs.remove(token)
        self.set_status(204)


class Artifact(tornado.web.StaticFileHandler):
    def initialize(self):
        super().initialize(path='/')

    async def head(self,token,filename):
        await self.get(token,filename,include_body=False)

    async def get(self,token,filename,include_body=True):
        job = self.settings['jobs'].get(token)
        allowed = {'input.mp4','scene.jpg','analysis.json','predictions.json','risk.csv','annotated.mp4'}
        if job['state']!='done' or filename not in allowed:
            raise tornado.web.HTTPError(404)
        self.root = job['temp'].name
        self.set_header('Cache-Control','private, no-store')
        if filename in ('predictions.json','risk.csv') or self.get_argument('download','')=='1':
            self.set_header('Content-Disposition',f'attachment; filename="{filename}"')
        await super().get(filename,include_body)


def make_app(jobs=None):
    return tornado.web.Application([
        (r'/api/info',Info), (r'/api/jobs',CreateJob),
        (r'/api/jobs/([a-f0-9]{32})/video',Upload),
        (r'/api/jobs/([a-f0-9]{32})',Job),
        (r'/api/jobs/([a-f0-9]{32})/files/([^/]+)',Artifact),
        (r'/(.*)',tornado.web.StaticFileHandler,dict(path=str(Path(__file__).parent/'public'),default_filename='index.html'))
    ],jobs=jobs or Jobs())


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8080)
    parser.add_argument('--host',default='127.0.0.1')
    args=parser.parse_args()
    app=make_app()
    app.listen(args.port,address=args.host,max_body_size=MAX_BYTES,max_buffer_size=16*1024*1024)
    tornado.ioloop.PeriodicCallback(app.settings['jobs'].prune,60000).start()
    print(f'RoadLens HTML website: http://{args.host}:{args.port}',flush=True)
    try:
        tornado.ioloop.IOLoop.current().start()
    except KeyboardInterrupt:
        pass
    finally:
        app.settings['jobs'].close()
