"""Single-user local PWA backend. Run with one uvicorn worker."""
from __future__ import annotations
import json, threading, uuid, zipfile, os, secrets
from dataclasses import asdict
from pathlib import Path
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
from fastapi import FastAPI, UploadFile, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from product_image_finder import load_products, search_candidates, download_bytes, inspect_image, textual_score, save_image
from config import SETTINGS

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.getenv('DATA_DIR', str(ROOT / 'output' / 'pwa')))
DATA.mkdir(parents=True, exist_ok=True)
lock = threading.RLock()
pool = ThreadPoolExecutor(max_workers=1)
app = FastAPI(title='Product Image Finder by Siddique Sayed')
APP_PASSWORD = os.getenv('APP_PASSWORD', '')
REQUIRE_AUTH = os.getenv('REQUIRE_AUTH', '').lower() == 'true'
if REQUIRE_AUTH and (not APP_PASSWORD or not os.getenv('SESSION_SECRET')):
    raise RuntimeError('Set APP_PASSWORD and SESSION_SECRET before hosting this app')

@app.middleware('http')
async def protect_api(request: Request, call_next):
    if APP_PASSWORD and request.url.path.startswith('/api/') and request.url.path not in ('/api/login', '/api/session', '/api/health'):
        if not request.session.get('authenticated'):
            return JSONResponse({'detail': 'Sign in to use your catalogue studio'}, status_code=401)
    return await call_next(request)

app.add_middleware(SessionMiddleware, secret_key=os.getenv('SESSION_SECRET') or secrets.token_urlsafe(32), https_only=REQUIRE_AUTH, same_site='lax', max_age=86400)

@app.get('/api/health')
def health(): return {'status': 'ok'}

@app.get('/api/session')
def session(request: Request):
    return {'authenticated': not APP_PASSWORD or bool(request.session.get('authenticated'))}

class Login(BaseModel):
    password: str

@app.post('/api/login')
def login(credentials: Login, request: Request):
    if APP_PASSWORD and not secrets.compare_digest(credentials.password.encode(), APP_PASSWORD.encode()):
        raise HTTPException(401, 'Incorrect password')
    request.session['authenticated'] = True
    return {'authenticated': True}


def folder(job_id):
    if not job_id.isalnum() or len(job_id) != 32:
        raise HTTPException(404, 'Job not found')
    return DATA / job_id

def read(job_id):
    with lock:
        try: return json.loads((folder(job_id) / 'job.json').read_text())
        except FileNotFoundError: raise HTTPException(404, 'Job not found')

def write(job):
    with lock:
        path = folder(job['id']) / 'job.json'
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(job))
        temp.replace(path)

# Interrupted jobs remain reviewable and can be retried after a restart.
for path in DATA.glob('*/job.json'):
    job = json.loads(path.read_text())
    if job['status'] in ('queued', 'running'):
        job['status'] = 'interrupted'
        write(job)

def run(job_id):
    job = read(job_id)
    job['status'] = 'running'; write(job)
    for index in range(len(job['products'])):
        job = read(job_id)
        product = job['products'][index]
        if product['status'] != 'pending': continue
        candidates = []
        errors = 0
        for candidate in search_candidates(product['name'], product['quantity']):
            try:
                raw = download_bytes(candidate.image_url)
                _, metrics = inspect_image(raw)
                for key, value in metrics.items(): setattr(candidate, key, value)
                candidate.text_score = textual_score(candidate, product['name'], product['quantity'])
                if candidate.width < SETTINGS.min_width or candidate.height < SETTINGS.min_height or candidate.white_ratio < SETTINGS.min_white_ratio: continue
                filename = f'{product["id"]}-{len(candidates)}.jpg'
                save_image(raw, folder(job_id) / filename)
                data = asdict(candidate)
                # No AI verification is performed in this first version.
                data.update(filename=filename, score=round(candidate.quality_score * .6 + candidate.text_score * .4, 1))
                candidates.append(data)
            except Exception: errors += 1
        candidates.sort(key=lambda c: c['score'], reverse=True)
        with lock:
            job = read(job_id)
            product = job['products'][index]
            product.update(candidates=candidates, selected=0 if candidates else None, status='review', reason='' if candidates else 'No usable image found. Search may be blocked or no image passed the quality checks. Upload an image to resolve this product.')
            job['processed'] = sum(p['status'] != 'pending' for p in job['products'])
            write(job)
    job = read(job_id); job['status'] = 'complete'; write(job)

@app.get('/api/jobs')
def jobs():
    with lock:
        return sorted([json.loads(p.read_text()) for p in DATA.glob('*/job.json')], key=lambda j:j['created'], reverse=True)

@app.post('/api/jobs')
async def create(file: UploadFile):
    suffix = Path(file.filename or '').suffix.lower()
    if suffix not in ('.csv', '.xlsx'): raise HTTPException(400, 'Upload a CSV or XLSX file')
    raw = await file.read(10_000_001)
    if len(raw) > 10_000_000: raise HTTPException(413, 'Maximum catalogue size is 10 MB')
    job_id = uuid.uuid4().hex; directory = folder(job_id); directory.mkdir()
    path = directory / ('catalogue' + suffix); path.write_bytes(raw)
    try: frame = load_products(path)
    except Exception as exc: raise HTTPException(400, str(exc))
    if not 0 < len(frame) <= 500: raise HTTPException(400, 'Upload between 1 and 500 unique products')
    import datetime
    job = {'id':job_id,'name':file.filename,'created':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'ready','processed':0,'products':[{'id':str(i),'name':r['Product Name'],'quantity':r['Quantity'],'status':'pending','candidates':[],'selected':None,'reason':''} for i,r in frame.iterrows()]}
    write(job); return job

@app.get('/api/jobs/{job_id}')
def get(job_id: str): return read(job_id)

@app.post('/api/jobs/{job_id}/start')
def start(job_id: str):
    with lock:
        job = read(job_id)
        if job['status'] not in ('ready','interrupted'): raise HTTPException(409,'This job has already started')
        job['status']='queued'; write(job)
        pool.submit(run,job_id)
        return job

class Decision(BaseModel):
    action: str
    candidate: int | None = None

@app.post('/api/jobs/{job_id}/products/{product_id}')
def decide(job_id: str, product_id: str, decision: Decision):
    with lock:
        job = read(job_id)
        product = next((p for p in job['products'] if p['id']==product_id), None)
        if not product: raise HTTPException(404,'Product not found')
        if product['status']=='pending': raise HTTPException(409,'Wait for the search to finish')
        if decision.action not in ('approve','reject','select'): raise HTTPException(400,'Invalid action')
        if decision.action in ('approve','select'):
            idx = decision.candidate if decision.candidate is not None else product['selected']
            if idx is None or not 0 <= idx < len(product['candidates']): raise HTTPException(400,'Choose an image first')
            product['selected']=idx
        product['status']={'approve':'approved','reject':'rejected','select':'review'}[decision.action]
        write(job); return job

@app.post('/api/jobs/{job_id}/products/{product_id}/image')
async def replace(job_id: str, product_id: str, file: UploadFile):
    raw = await file.read(15_000_001)
    if len(raw)>15_000_000: raise HTTPException(413,'Maximum image size is 15 MB')
    with lock:
        job=read(job_id)
        p=next((p for p in job['products'] if p['id']==product_id),None)
        if not p: raise HTTPException(404,'Product not found')
        if p['status']=='pending': raise HTTPException(409,'Wait for search to finish')
        filename=f'{product_id}-manual-{uuid.uuid4().hex}.jpg'
        try: save_image(raw,folder(job_id)/filename)
        except Exception: raise HTTPException(400,'Upload a valid image')
        p['candidates'].append({'filename':filename,'score':None,'title':'Uploaded image','page_url':'','width':0,'height':0,'white_ratio':0,'reason':'Manually supplied; not automatically verified'})
        p.update(selected=len(p['candidates'])-1,status='review',reason='')
        write(job); return job

@app.get('/api/jobs/{job_id}/images/{filename}')
def image(job_id: str, filename: str):
    read(job_id)
    if Path(filename).name!=filename or not filename.endswith('.jpg'): raise HTTPException(404)
    path=folder(job_id)/filename
    if not path.exists(): raise HTTPException(404)
    return FileResponse(path)

@app.get('/api/jobs/{job_id}/export/{kind}')
def export(job_id: str,kind: str):
    job=read(job_id); records=[]
    for p in job['products']:
        c=p['candidates'][p['selected']] if p['selected'] is not None else {}
        records.append({'Product Name':p['name'],'Quantity':p['quantity'],'Status':p['status'],'Image Filename':c.get('filename',''),'Source Page':c.get('page_url',''),'Score':c.get('score','')})
    frame=pd.DataFrame(records); stream=BytesIO()
    if kind=='csv':
        # Prevent spreadsheet formula execution in user-supplied text.
        frame=frame.map(lambda v: "'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v)
        stream.write(frame.to_csv(index=False).encode()); mime='text/csv'
    elif kind=='xlsx':
        frame=frame.map(lambda v: "'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v)
        frame.to_excel(stream,index=False); mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    elif kind=='zip':
        with zipfile.ZipFile(stream,'w') as archive:
            for p in job['products']:
                if p['status']=='approved':
                    name=p['candidates'][p['selected']]['filename']
                    archive.write(folder(job_id)/name,name)
        mime='application/zip'
    else: raise HTTPException(404)
    return Response(stream.getvalue(),media_type=mime,headers={'Content-Disposition':f'attachment; filename="catalogue.{kind}"'})

if (ROOT/'frontend/dist').exists():
    app.mount('/',StaticFiles(directory=ROOT/'frontend/dist',html=True),name='frontend')
