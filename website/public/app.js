'use strict';
const $ = id => document.getElementById(id);
const state = {info:null,file:null,url:null,result:null,job:null,busy:false};
const label = name => name.replaceAll('_',' ');
const seconds = n => `${Number(n).toFixed(1)}s`;
const el = (tag,text,className) => {const node=document.createElement(tag); if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;};
function fail(message){$('error').textContent=message;$('error').hidden=false;}
async function api(path,options={}){const response=await fetch(path,options);if(!response.ok){let detail;try{detail=await response.json();}catch{}if(response.status===413)showUploadLimit();throw new Error(detail?.error||`Request failed (${response.status})`);}return response.status===204?null:response.json();}
function progress(value,text){$('progress-area').hidden=false;$('progress').value=value;$('progress-number').textContent=`${Math.round(value)}%`;$('progress-text').textContent=text;}
function selectedProfile(){return state.info?.profiles?.find(p=>p.id===$('profile').value);}
function busy(value){state.busy=value;const ready=!!selectedProfile()?.config;$('analyze').disabled=value||!state.file||!state.info?.weights_ready||!ready;$('video-file').disabled=value;$('profile').disabled=value||!state.info?.profiles?.some(p=>p.config);$('config').disabled=value||!ready;$('render').disabled=value;$('analyze').textContent=value?'Analysis in progress…':'Analyze footage ↗';}
function setupProfiles(profiles){
  const available=Array.isArray(profiles)?profiles:[];
  const modes=[
    {id:'daytime',label:'Daytime framing',sources:['daytime.json','camera.json']},
    {id:'nighttime',label:'Nighttime framing',sources:['nighttime.json','uncalibrated.json']}
  ];
  state.info.profiles=modes.map(mode=>{
    const source=mode.sources.map(id=>available.find(p=>p.id===id)).find(Boolean);
    const config=source?.config;
    return {id:mode.id,label:mode.label,config:config&&typeof config==='object'&&!Array.isArray(config)?config:null};
  });
  $('profile').replaceChildren(...state.info.profiles.map(p=>{
    const option=el('option',p.label);option.value=p.id;option.disabled=!p.config;return option;
  }));
  const initial=state.info.profiles.find(p=>p.config);
  $('profile').value=initial?.id||'daytime';
  applyProfile();
}
function applyProfile(){
  const profile=selectedProfile();
  if(!profile?.config){
    $('config').value='';
    $('profile-note').textContent='The server has not provided a configuration for this framing.';
    busy(state.busy);return;
  }
  $('config').value=JSON.stringify(profile.config,null,2);
  const framing=profile.id==='daytime'?'Daytime':'Nighttime';
  $('profile-note').textContent=profile.config.calibrated
    ?`${framing} framing selected. Use this geometry only for its matching camera view.`
    :`${framing} framing selected. Object tracking is available; configure the camera geometry to enable event rules.`;
  busy(state.busy);
}
function setVideo(url){$('video').src=url;$('video').hidden=false;$('video-empty').hidden=true;$('codec-note').hidden=true;}
function uploadLimitBytes(){return Number(state.info?.max_bytes)||200000000;}
function showUploadLimit(){
  const limit=uploadLimitBytes()/1000000;
  $('upload-limit-description').textContent=`This video exceeds the ${limit} MB hosted upload limit. Choose a smaller file or run the project locally.`;
  const dialog=$('upload-limit-dialog');
  if(!dialog.open)dialog.showModal();
}
function rejectFile(message,oversize=false){
  state.file=null;
  $('video-file').value='';
  $('file-name').textContent='Drop your video here';
  $('file-detail').textContent='or click to browse your files';
  busy(false);
  fail(message);
  if(oversize)showUploadLimit();
}
function choose(file){if(!file||state.busy)return;$('error').hidden=true;if(file.size>uploadLimitBytes())return rejectFile(`This file exceeds the ${uploadLimitBytes()/1000000} MB upload limit.`,true);if(!file.name.toLowerCase().endsWith('.mp4'))return rejectFile('Please choose an MP4 video.');if(!state.info)return fail('The analysis server is not connected.');if(file.size===0)return rejectFile('The selected file is empty.');state.file=file;state.result=null;state.job=null;$('results').hidden=true;$('progress-area').hidden=true;if(state.url)URL.revokeObjectURL(state.url);state.url=URL.createObjectURL(file);setVideo(state.url);$('file-name').textContent=file.name;$('file-detail').textContent=`${(file.size/1000000).toFixed(1)} MB · ready to analyze`;$('video-caption').textContent=file.name;for(const id of ['duration','events','time','speed'])$('metric-'+id).textContent='—';busy(false);}
$('video-file').addEventListener('change',e=>choose(e.target.files[0]));
$('dropzone').addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();$('video-file').click();}});
for(const type of ['dragenter','dragover'])$('dropzone').addEventListener(type,e=>{e.preventDefault();$('dropzone').classList.add('dragover');});
for(const type of ['dragleave','drop'])$('dropzone').addEventListener(type,e=>{e.preventDefault();$('dropzone').classList.remove('dragover');if(type==='drop')choose(e.dataTransfer.files[0]);});
$('video').addEventListener('error',()=>{$('codec-note').hidden=false;});
$('profile').addEventListener('change',applyProfile);
$('save-config').addEventListener('click',()=>{try{const data=JSON.parse($('config').value);const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=el('a');a.href=url;a.download='camera.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch{fail('The camera configuration is not valid JSON.');}});
function uploadFile(id,file){return new Promise((resolve,reject)=>{const xhr=new XMLHttpRequest();xhr.open('PUT',`/api/jobs/${id}/video`);xhr.setRequestHeader('Content-Type','video/mp4');xhr.upload.onprogress=e=>{if(e.lengthComputable)progress(100*e.loaded/e.total,'Uploading to analysis server');};xhr.onload=()=>{if(xhr.status>=200&&xhr.status<300)resolve();else{if(xhr.status===413)showUploadLimit();let message='Upload failed.';try{message=JSON.parse(xhr.responseText).error||message;}catch{}reject(new Error(message));}};xhr.onerror=()=>reject(new Error('Connection lost during upload. Please try again.'));xhr.send(file);});}
$('analyze').addEventListener('click',async()=>{if(state.busy||!state.file)return;let config;try{config=JSON.parse($('config').value);}catch{return fail('The camera configuration is not valid JSON.');}$('error').hidden=true;state.result=null;$('results').hidden=true;busy(true);try{progress(0,'Preparing upload');const job=await api('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:state.file.name,size:state.file.size,config,render:$('render').checked})});state.job=job.id;await uploadFile(job.id,state.file);sessionStorage.setItem('roadlens-job',job.id);await poll(job.id);}catch(error){fail(error.message);if(state.job)await fetch(`/api/jobs/${state.job}`,{method:'DELETE'}).catch(()=>{});busy(false);}});
async function poll(id){for(;;){const job=await api(`/api/jobs/${id}`);if(job.state==='failed')throw new Error(job.error||'Analysis failed.');if(job.state==='done'){progress(100,'Analysis complete');await showResult(id,job);busy(false);return;}progress(job.progress,job.state==='rendering'?'Exporting annotated video…':'Detecting, tracking and analyzing');await new Promise(resolve=>setTimeout(resolve,1000));}}
function downloadLink(text,path){const a=el('a',text,'button secondary small');a.href=path;a.setAttribute('download','');return a;}
function checkedRisk(rows){
  let maximum=0,last=-1;
  if(!Array.isArray(rows))throw new Error('Invalid risk output: expected [timestamp_seconds, score_0_to_1] rows.');
  rows.forEach((row,index)=>{
    if(!Array.isArray(row)||row.length!==2||!row.every(Number.isFinite)||row[0]<0||row[0]<=last||row[1]<0||row[1]>1)
      throw new Error(`Invalid risk row ${index}. Time is column 1; the score in column 2 must be between 0 and 1. Download/check the server output.`);
    last=row[0];maximum=Math.max(maximum,row[1]);
  });
  return maximum;
}
async function showResult(id,job){
  const root=`/api/jobs/${id}/files/`,data=await api(root+'analysis.json');
  const maximum=checkedRisk(data.risk); // Never hide invalid server values by clipping a chart.
  state.result=data;state.job=id;$('results').hidden=false;
  $('metric-duration').textContent=seconds(data.meta.duration);$('metric-events').textContent=data.events.length;
  $('metric-time').textContent=seconds(data.meta.elapsed);$('metric-speed').textContent=`${data.meta.realtime_factor.toFixed(2)}×`;
  $('video-caption').textContent=job.name;if(!state.file)setVideo(root+'input.mp4');
  const notes=[];
  if(!data.calibrated)notes.push('This camera is uncalibrated. Object tracks are shown; event rules are disabled.');
  else notes.push('Event candidates depend on your camera configuration. Review them against the footage.');
  if(!data.events.length)notes.push('No supported events detected. This does not prove the video is event-free.');
  if(job.warning)notes.push(job.warning);
  $('result-note').textContent=notes.join(' ');
  $('downloads').replaceChildren(downloadLink('Predictions ↓',root+'predictions.json'),downloadLink('Risk CSV ↓',root+'risk.csv'),downloadLink('Full analysis ↓',root+'analysis.json?download=1'));
  if(data.website_export_available)$('downloads').append(downloadLink('Annotated video ↓',root+'annotated.mp4?download=1'));
  $('scene-preview').src=root+'scene.jpg';
  $('risk-note').textContent=data.risk_enabled
    ?`Maximum score: ${maximum.toFixed(3)} / 1.000 · horizontal axis: seconds · JSON rows: [seconds, score] · experimental, not calibrated probability`
    :'Risk estimation was disabled for this run. The score remains zero.';
  renderEvents(data);drawCharts();$('results').scrollIntoView({behavior:'smooth',block:'start'});
}
function seek(t){$('video').currentTime=t;$('video-stage').scrollIntoView({behavior:'smooth',block:'center'});}
function renderEvents(data){$('events').replaceChildren();$('timeline').replaceChildren();const types=[...new Set(data.events.map(x=>x[2]))];if(!types.length)$('timeline').append(el('p','No event segments to display.','hint'));for(const type of types){const row=el('div',undefined,'timeline-row');row.append(el('span',label(type)));const track=el('div',undefined,'timeline-track');for(const [start,end,name]of data.events.filter(x=>x[2]===type)){const b=el('button',undefined,'event-bar');b.style.left=`${100*start/data.meta.duration}%`;b.style.width=`${100*(end-start)/data.meta.duration}%`;b.title=`${label(name)}: ${seconds(start)} – ${seconds(end)}`;b.setAttribute('aria-label',b.title);b.onclick=()=>seek(start);track.append(b);}row.append(track);$('timeline').append(row);}const axis=el('div',undefined,'timeline-axis');axis.append(el('span','0s'),el('span',seconds(data.meta.duration)));$('timeline').append(axis);for(const[start,end,type]of data.events){const row=el('tr');row.append(el('td',label(type)),el('td',seconds(start)),el('td',seconds(end)),el('td',seconds(end-start)));const cell=el('td');const button=el('button','View ↗','seek');button.onclick=()=>seek(start);cell.append(button);row.append(cell);$('events').append(row);}}
function lineChart(id,series,maxY,duration){const canvas=$(id),width=canvas.clientWidth||400,height=canvas.clientHeight||180,dpr=window.devicePixelRatio||1;canvas.width=width*dpr;canvas.height=height*dpr;const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);const left=35,top=12,right=10,bottom=26,w=width-left-right,h=height-top-bottom;ctx.font='10px system-ui';for(let i=0;i<=4;i++){const y=top+h*(1-i/4);ctx.strokeStyle='#e7ede3';ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(width-right,y);ctx.stroke();ctx.fillStyle='#7d8c7d';ctx.fillText((maxY*i/4).toFixed(maxY===1?2:0),0,y+3);}ctx.fillText('0s',left,height-5);ctx.fillText(`${duration.toFixed(0)}s`,width-40,height-5);for(const s of series){ctx.strokeStyle=s.color;ctx.lineWidth=1.6;ctx.beginPath();const stride=Math.max(1,Math.floor(s.points.length/900));s.points.filter((_,i)=>i%stride===0).forEach(([t,v],i)=>{const x=left+w*t/duration,y=top+h*(1-Math.max(0,Math.min(maxY,v))/maxY);if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);});ctx.stroke();}}
function drawCharts(){const d=state.result;if(!d)return;lineChart('risk-chart',[{points:d.risk,color:'#277c63'}],1,d.meta.duration);const vehicles=d.counts.map(r=>[r.time,['car','bus','truck','motorcycle','bicycle'].reduce((n,k)=>n+(r[k]||0),0)]);const people=d.counts.map(r=>[r.time,r.person||0]);let max=1;for(const p of [...vehicles,...people])max=Math.max(max,p[1]);lineChart('count-chart',[{points:vehicles,color:'#277c63'},{points:people,color:'#db9450'}],max,d.meta.duration);}
function drawBoxes(){const video=$('video'),canvas=$('boxes'),rect=video.getBoundingClientRect(),dpr=window.devicePixelRatio||1;const width=rect.width,height=rect.height;if(canvas.width!==Math.round(width*dpr)||canvas.height!==Math.round(height*dpr)){canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);}const ctx=canvas.getContext('2d');ctx.clearRect(0,0,canvas.width,canvas.height);const d=state.result;if(d&&$('overlay').checked&&video.videoWidth){const t=video.currentTime;let lo=0,hi=d.tracks.length-1;while(lo<=hi){const mid=(lo+hi)>>1;if(d.tracks[mid].t<=t)lo=mid+1;else hi=mid-1;}const frame=d.tracks[hi];if(frame&&t-frame.t<.5){const scale=Math.min(width/video.videoWidth,height/video.videoHeight);const vw=video.videoWidth*scale,vh=video.videoHeight*scale,ox=(width-vw)/2,oy=(height-vh)/2;ctx.save();ctx.scale(dpr,dpr);ctx.font='11px system-ui';ctx.lineWidth=1.5;for(const o of(frame.display_objects||frame.objects)){if(o.predicted&&t>o.expires_at)continue;const[x1,y1,x2,y2]=o.box;ctx.strokeStyle=o.predicted?'#f5b05e':'#97f0bc';ctx.setLineDash(o.predicted?[4,3]:[]);ctx.strokeRect(ox+x1*vw,oy+y1*vh,(x2-x1)*vw,(y2-y1)*vh);ctx.fillStyle=ctx.strokeStyle;ctx.fillText(`${o.class} #${o.id}${o.predicted?' · predicted':''}`,ox+x1*vw,Math.max(12,oy+y1*vh-4));}ctx.restore();}}requestAnimationFrame(drawBoxes);}
window.addEventListener('resize',drawCharts);requestAnimationFrame(drawBoxes);
function renderTeam(team){
  $('team-name').textContent=`Meet ${team.name||'JAM'}.`;
  $('team-members').replaceChildren();
  for(const member of team.members||[]){
    const card=el('article',undefined,'member');
    const photo=el('div',undefined,'member-photo');
    photo.setAttribute('aria-hidden','true');
    const source=(member.photo||'').trim();
    if(source){
      const url=new URL(source,location.href);
      if(['http:','https:'].includes(url.protocol)){
        const img=el('img');img.src=url.href;img.alt=member.name;img.loading='lazy';
        img.addEventListener('error',()=>{img.remove();photo.setAttribute('aria-hidden','true');});
        photo.removeAttribute('aria-hidden');photo.append(img);
      }
    }
    card.append(photo,el('h3',member.name),el('p',member.role,'member-role'),el('p',member.contribution||member.contributions||''));
    const links=el('div',undefined,'member-links');
    for(const [label,value] of Object.entries({GitHub:member.github,LinkedIn:member.linkedin,Portfolio:member.portfolio,...member.links})){
      const url=(value||'').trim();if(!/^https?:\/\//i.test(url))continue;
      const a=el('a',label+' ↗');a.href=url;a.target='_blank';a.rel='noopener noreferrer';links.append(a);
    }
    card.append(links);$('team-members').append(card);
  }
}
async function init(){try{state.info=await api('/api/info');$('connection').classList.add('ready');$('connection').lastChild.textContent=' Model server connected';setupProfiles(state.info.profiles);if(!state.info.weights_ready)fail('Model weights are missing from the server. Please contact the site owner.');renderTeam(state.info.team);const previous=sessionStorage.getItem('roadlens-job');if(previous){state.job=previous;busy(true);try{await poll(previous);}catch{sessionStorage.removeItem('roadlens-job');busy(false);}}}catch(error){$('connection').lastChild.textContent=' Server offline';fail('The analysis server is unavailable. Please refresh the page or try again later.');}}
init();
