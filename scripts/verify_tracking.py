"""Run a short real-video check and export a review clip without touching originals."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import cv2
from src.config import load_config
from src.pipeline import analyze
from src.output import validate_prediction
from src.render import render_video,draw_scene

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--video',type=Path,required=True)
    parser.add_argument('--camera',type=Path,required=True)
    parser.add_argument('--seconds',type=float,default=15)
    parser.add_argument('--out',type=Path,default=Path('outputs/tracking-review'))
    args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    source=args.out/'input.mp4'
    cap=cv2.VideoCapture(str(args.video))
    fps=cap.get(cv2.CAP_PROP_FPS)
    if not cap.isOpened() or fps<=0:
        raise ValueError('Cannot open source video')
    width,height=int(cap.get(3)),int(cap.get(4))
    out_width=1280
    out_height=round(height*out_width/width/2)*2
    writer=cv2.VideoWriter(str(source),cv2.VideoWriter_fourcc(*'mp4v'),fps,(out_width,out_height))
    if not writer.isOpened(): raise RuntimeError('Cannot open review video encoder')
    try:
        for i in range(round(args.seconds*fps)):
            ok,frame=cap.read()
            if not ok: break
            writer.write(cv2.resize(frame,(out_width,out_height)))
    finally:
        writer.release(); cap.release()
    print('Review clip extracted; running actual YOLO + ByteTrack…',flush=True)
    config=load_config(args.camera)
    preview=cv2.VideoCapture(str(source))
    ok,first=preview.read()
    preview.release()
    if ok: cv2.imwrite(str(args.out/'calibration.jpg'),draw_scene(first,config))
    result=analyze(source,config)
    validate_prediction(result['events'],result['risk'],result['meta']['duration'])
    (args.out/'review.analysis.json').write_text(json.dumps(result,allow_nan=False))
    render_video(source,result,args.out/'review.annotated.mp4')
    summary=dict(meta=result['meta'],events=result['events'],
                 score_range=[min(x[1] for x in result['risk']),max(x[1] for x in result['risk'])],
                 observations=sum(len(x['objects']) for x in result['tracks']),
                 predicted_display_boxes=sum(o.get('predicted',False) for x in result['tracks'] for o in x['display_objects']))
    (args.out/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False))
    print(json.dumps(summary,indent=2))
