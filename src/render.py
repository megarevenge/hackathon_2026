"""Export boxes and events; ffmpeg enables browser-compatible H.264."""
import shutil
import subprocess
from pathlib import Path


def draw_scene(frame,config):
    import cv2
    import numpy as np
    image=frame.copy()
    h,w=image.shape[:2]
    groups=[('road',[config['road']],(70,220,100)),('crossing',config['crosswalks'],(255,180,40)),
            ('queue',config['queue_zones'],(200,100,230)),('excluded',config['excluded_zones'],(100,100,240))]
    for label,polygons,color in groups:
        for i,polygon in enumerate(polygons):
            if not polygon: continue
            points=np.array([[round(x*w),round(y*h)] for x,y in polygon],dtype=np.int32)
            cv2.polylines(image,[points],True,color,2)
            cv2.putText(image,f'{label} {i}',tuple(points[0]),cv2.FONT_HERSHEY_SIMPLEX,.5,color,1)
    return image


def render_video(source, analysis, target):
    import cv2
    target = Path(target)
    intermediate = target.with_name(target.stem+'.intermediate.mp4')
    target.parent.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(source))
    meta = analysis['meta']
    writer = cv2.VideoWriter(str(intermediate), cv2.VideoWriter_fourcc(*'mp4v'), meta['fps'],
                             (meta['width'], meta['height']))
    if not writer.isOpened():
        cap.release()
        raise RuntimeError('Video encoder could not be opened.')
    index, sample_index = 0, 0
    samples = analysis['tracks']
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            t = index/meta['fps']
            while sample_index+1 < len(samples) and samples[sample_index+1]['t'] <= t:
                sample_index += 1
            if samples and t-samples[sample_index]['t'] < .5:
                for obj in samples[sample_index].get('display_objects',samples[sample_index]['objects']):
                    if obj.get('predicted') and t>obj.get('expires_at',t):
                        continue
                    box = obj['box']
                    x1,x2 = int(box[0]*meta['width']), int(box[2]*meta['width'])
                    y1,y2 = int(box[1]*meta['height']), int(box[3]*meta['height'])
                    color = (0,180,255) if obj.get('predicted') else (190,220,70)
                    cv2.rectangle(frame,(x1,y1),(x2,y2),color,2)
                    suffix = ' predicted' if obj.get('predicted') else ''
                    cv2.putText(frame,f"{obj['class']} #{obj['id']}{suffix}",(x1,max(20,y1-6)),cv2.FONT_HERSHEY_SIMPLEX,.5,color,1)
            active = [label for start,end,label in analysis['events'] if start <= t < end]
            caption = f'{t:.1f}s | '+(', '.join(active) if active else 'No active supported event')
            cv2.putText(frame,caption,(15,30),cv2.FONT_HERSHEY_SIMPLEX,.65,(255,255,255),2)
            writer.write(frame)
            index += 1
    finally:
        cap.release()
        writer.release()
    if index < meta['frames']-2:
        raise ValueError('Annotated export encountered an incomplete video.')
    if shutil.which('ffmpeg'):
        subprocess.run(['ffmpeg','-v','error','-y','-i',str(intermediate),'-an','-c:v','libx264',
                        '-pix_fmt','yuv420p','-movflags','+faststart',str(target)],check=True,capture_output=True)
        intermediate.unlink()
    else:
        intermediate.replace(target)
    return target
