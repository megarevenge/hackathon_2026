"""Fast rule tuning on saved real detections, without rerunning YOLO."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.config import load_config
from src.events import RuleEngine
from src.risk import smooth_score, bounded_score
from src.tracking import DisplayTracker
from src.output import validate_prediction


def replay(data, config):
    engine, display = RuleEngine(config), DisplayTracker(config['display_hold_seconds'])
    sampled = []
    last_t,score = -1/config['sample_fps'],0.0
    for sample in data['tracks']:
        t = sample['t']
        raw = engine.step(sample['objects'],t,sample.get('signals',{}))
        score = smooth_score(score,raw,t-last_t)
        sampled.append([t,score])
        sample['display_objects'] = display.update(sample['objects'],t)
        last_t = t
    risk,index = [],0
    for frame in range(data['meta']['frames']):
        t = frame/data['meta']['fps']
        while index+1<len(sampled) and sampled[index+1][0]<=t:
            index += 1
        risk.append([round(t,6),round(bounded_score(sampled[index][1] if sampled else 0),6)])
    data.update(events=engine.finish(data['meta']['duration']),risk=risk,config=config,
                calibrated=config.get('calibrated',False),risk_enabled=config['risk_enabled'],
                risk_columns=['timestamp_seconds','score_0_to_1'])
    data['replayed_from_saved_detections'] = True
    validate_prediction(data['events'],data['risk'],data['meta']['duration'])
    return data


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('analysis',type=Path)
    parser.add_argument('--camera',required=True,type=Path)
    parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--pred-out',type=Path,help='Optional competition-shaped predictions JSON')
    parser.add_argument('--video-name',help='Original MP4 filename, required with --pred-out')
    args=parser.parse_args()
    if args.pred_out and (not args.video_name or Path(args.video_name).name!=args.video_name):
        parser.error('--pred-out requires --video-name with a filename, not a path')
    if args.out.resolve()==args.analysis.resolve():
        parser.error('Use a new output path to preserve the original analysis.')
    result=replay(json.loads(args.analysis.read_text()),load_config(args.camera))
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,allow_nan=False))
    if args.pred_out:
        args.pred_out.parent.mkdir(parents=True,exist_ok=True)
        prediction={'team':'Team name pending','videos':{args.video_name:{k:result[k] for k in ('events','risk')}}}
        args.pred_out.write_text(json.dumps(prediction,allow_nan=False))
    print('Events:',result['events'])
    print('Score range:',min(x[1] for x in result['risk']),max(x[1] for x in result['risk']))
