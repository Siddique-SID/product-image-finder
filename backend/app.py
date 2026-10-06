"""Private customer catalogue API. Run with one uvicorn worker."""
from __future__ import annotations
import json, threading, uuid, zipfile, os, secrets, shutil, tempfile, re, hashlib, time
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
from fastapi.responses import HTMLResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field
from contextvars import ContextVar
from backend.storage import Store
from backend.accounts import hash_password, verify, throttle
from backend.invitations import invitation_digest, send_invitation
from urllib.parse import urlencode
SERVERLESS = os.getenv('VERCEL') == '1'
if SERVERLESS:
    os.environ.setdefault('MAX_CANDIDATES', '3')
    os.environ.setdefault('REQUEST_TIMEOUT', '5')
from product_image_finder import load_products, search_candidates, download_bytes, inspect_image, textual_score, save_image, SearchUnavailable
from config import SETTINGS

ROOT = Path(__file__).resolve().parents[1]
DATA = Path('/tmp/product-studio' if SERVERLESS else os.getenv('DATA_DIR', str(ROOT / 'output' / 'pwa')))
DATA.mkdir(parents=True, exist_ok=True)
lock = threading.RLock()
pool = ThreadPoolExecutor(max_workers=1)
app = FastAPI(title='Product Image Finder by Siddique Sayed')
APP_PASSWORD = os.getenv('APP_PASSWORD', '')
REQUIRE_AUTH = SERVERLESS or os.getenv('REQUIRE_AUTH', '').lower() == 'true'
if REQUIRE_AUTH and (not APP_PASSWORD or not os.getenv('SESSION_SECRET')):
    raise RuntimeError('Set APP_PASSWORD and SESSION_SECRET before hosting this app')
if SERVERLESS and not os.getenv('DATABASE_URL'):
    raise RuntimeError('Set DATABASE_URL before deploying to Vercel; temporary storage is not supported')

store = Store(DATA / 'studio.sqlite3', os.getenv('DATABASE_URL', ''))
current_owner = ContextVar('current_owner', default='legacy')
public_routes = {'/api/login', '/api/register', '/api/recover', '/api/session', '/api/health', '/api/app-update'}


@app.exception_handler(RequestValidationError)
async def readable_validation(request: Request, error: RequestValidationError):
    if request.url.path == '/api/invites':
        message = (
            'Please use Invite by email and enter a recipient email address. '
            'If you still see Invite user, update the app at '
            'https://sid-image-finder.vercel.app/api/app-update'
        )
    else:
        messages = []
        for item in error.errors():
            field = str(item['loc'][-1]).replace('_', ' ')
            text = item['msg']
            messages.append(f'{field.capitalize()}: {text}')
        message = '; '.join(messages) or 'Please check the submitted fields.'
    # Never expose submitted input (which can include passwords) in errors.
    return JSONResponse({'detail': message}, status_code=422)


@app.get('/api/app-update', response_class=HTMLResponse)
def app_update():
    return HTMLResponse('''<!doctype html><html lang="en"><head>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <meta name="robots" content="noindex"><title>Update Product Image Finder</title>
    <style>body{font:18px system-ui;background:#f5f6f8;color:#193b2e;margin:0;padding:24px}
    main{max-width:540px;margin:12vh auto;background:white;padding:32px;border-radius:20px}
    button,a{background:#073b2b;color:white;border:0;border-radius:8px;padding:14px 20px;font:inherit;cursor:pointer}
    a{display:none;text-decoration:none}</style></head><body><main>
    <h1>Update your app</h1><p>This refreshes the app files saved by your browser.
    Your account, password and catalogues stay unchanged.</p>
    <button id="update">Update app</button><p id="status" role="status"></p>
    <a id="open" href="/">Open updated app</a></main><script>
    document.getElementById('update').onclick=async function(){
      this.disabled=true;const status=document.getElementById('status');
      status.textContent='Updating app files…';
      try{
        if('serviceWorker' in navigator){
          const registrations=await navigator.serviceWorker.getRegistrations();
          await Promise.all(registrations.map(r=>r.unregister()));
        }
        if('caches' in window){
          const names=await caches.keys();
          await Promise.all(names.filter(n=>n.startsWith('workbox-')||n==='app-pages').map(n=>caches.delete(n)));
        }
        status.textContent='Ready. Open the updated app to invite by email.';
        this.style.display='none';document.getElementById('open').style.display='inline-block';
      }catch(e){status.textContent='Close all app tabs and reopen the app to finish updating.';this.disabled=false}
    };
    </script></body></html>''', headers={'Cache-Control': 'no-store'})


def identity(request):
    uid = request.session.get('user_id')
    if uid:
        user = store.user(uid=uid)
        if user and user['version'] == request.session.get('version'):
            return {'id': uid, 'name': user['name'], 'email': user['email']}
        return None
    if request.session.get('authenticated') or not APP_PASSWORD:
        return {'id': 'legacy', 'name': 'Owner workspace', 'email': ''}
    return None

@app.middleware('http')
async def protect_api(request: Request, call_next):
    user = identity(request) if request.url.path.startswith('/api/') else None
    if request.url.path.startswith('/api/'):
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            expected = str(request.base_url).rstrip('/')
            if origin and origin != expected:
                return JSONResponse({'detail': 'Request origin is not allowed'}, status_code=403)
        if request.url.path not in public_routes and not user:
            return JSONResponse({'detail': 'Sign in to use your catalogue studio'}, status_code=401)
    token = current_owner.set(user['id'] if user else None)
    try:
        response = await call_next(request)
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'private, no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['X-Frame-Options'] = 'DENY'
        return response
    finally:
        current_owner.reset(token)

app.add_middleware(SessionMiddleware, secret_key=os.getenv('SESSION_SECRET') or secrets.token_urlsafe(32), https_only=REQUIRE_AUTH, same_site='lax', max_age=86400)

@app.get('/api/health')
def health():
    try:
        with store.connection() as db: db.execute('SELECT 1').fetchone()
    except Exception:
        raise HTTPException(503, 'Database is unavailable')
    return {'status': 'ok'}

@app.get('/api/session')
def session(request: Request):
    user = identity(request)
    return {'authenticated': bool(user), 'user': user, 'durable': bool(os.getenv('DATABASE_URL')), 'registration': bool(APP_PASSWORD), 'request_processing': SERVERLESS, 'upload_limit': 4_000_000 if SERVERLESS else 10_000_000}

class Login(BaseModel):
    email: str = Field(default='', max_length=254)
    password: str = Field(max_length=128)

@app.post('/api/login')
def login(credentials: Login, request: Request):
    throttle(request)
    email = credentials.email.strip().lower()
    if email:
        user = store.user(email=email)
        if not user or not verify(credentials.password, user['password']):
            raise HTTPException(401, 'Incorrect email or password')
        request.session.clear()
        request.session.update(user_id=user['id'], version=user['version'])
    else:
        if APP_PASSWORD and not secrets.compare_digest(credentials.password.encode(), APP_PASSWORD.encode()):
            raise HTTPException(401, 'Incorrect password')
        request.session.clear()
        request.session['authenticated'] = True
    return session(request)

class Registration(Login):
    name: str = Field(min_length=1, max_length=80)
    access_code: str = Field(max_length=128)

@app.post('/api/register')
def register(credentials: Registration, request: Request):
    throttle(request)
    if not APP_PASSWORD:
        raise HTTPException(403, 'Registration is disabled in local mode')
    email = credentials.email.strip().lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise HTTPException(400, 'Enter a valid email address')
    if len(credentials.password) < 10:
        raise HTTPException(400, 'Use a password with at least 10 characters')
    recovery = secrets.token_urlsafe(24)
    uid = uuid.uuid4().hex
    with lock:
        if store.user(email=email): raise HTTPException(409, 'An account with this email already exists')
        if not credentials.name.strip(): raise HTTPException(400, 'Enter your name')
        redeemed = store.redeem(invitation_digest(credentials.access_code, email), time.time(), uid, email, credentials.name.strip(), hash_password(credentials.password), hash_password(recovery))
        if not redeemed: raise HTTPException(403, 'This invitation is invalid, expired or already used')
    request.session.clear()
    request.session.update(user_id=uid, version=1)
    return {**session(request), 'recovery_code': recovery}

class Recovery(Login):
    recovery_code: str = Field(max_length=128)

@app.post('/api/recover')
def recover(credentials: Recovery, request: Request):
    throttle(request)
    user = store.user(email=credentials.email.strip().lower())
    if not user or not verify(credentials.recovery_code, user['recovery']):
        raise HTTPException(401, 'Incorrect email or recovery code')
    if len(credentials.password) < 10: raise HTTPException(400, 'Use at least 10 characters')
    recovery = secrets.token_urlsafe(24)
    store.reset_user(user['id'], hash_password(credentials.password), hash_password(recovery))
    request.session.clear()
    return {'recovery_code': recovery}

class Invitation(BaseModel):
    email: str = Field(max_length=254)

@app.post('/api/invites')
def invite(recipient: Invitation, request: Request):
    if current_owner.get() != 'legacy' or not APP_PASSWORD:
        raise HTTPException(403, 'Only the studio owner can invite customers')
    throttle(request)
    email = recipient.email.strip().lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise HTTPException(400, 'Enter a valid email address')
    if store.user(email=email):
        raise HTTPException(409, 'This person already has an account. They can sign in.')
    code = secrets.token_urlsafe(32)
    store.invite(invitation_digest(code, email), time.time() + 7 * 86400)
    base = os.getenv('PUBLIC_APP_URL', 'https://sid-image-finder.vercel.app').rstrip('/')
    # Fragments never reach the web server, preventing tokens in access logs.
    link = base + '/#' + urlencode({'invite': code, 'email': email})
    delivery = send_invitation(email, link)
    return {'email': email, 'link': link, 'delivery': delivery, 'expires_days': 7}

@app.post('/api/logout')
def logout(request: Request):
    request.session.clear()
    return {'authenticated': False}


def folder(job_id):
    if not job_id.isalnum() or len(job_id) != 32:
        raise HTTPException(404, 'Job not found')
    return DATA / job_id

def read(job_id, internal=False):
    folder(job_id)
    job = store.read(job_id)
    if not job or (not internal and job.get('owner', 'legacy') != current_owner.get()):
        raise HTTPException(404, 'Job not found')
    return job


def write(job):
    store.write(job)


# Migrate existing owner catalogues and images without exposing them to customers.
for path in DATA.glob('*/job.json'):
    old = json.loads(path.read_text())
    if not store.read(old['id']):
        old['owner'] = 'legacy'
        for image_path in path.parent.glob('*.jpg'):
            store.put_image(old['id'], image_path.name, image_path.read_bytes())
        write(old)
for job in ([] if SERVERLESS else store.jobs()):
    if job['status'] in ('queued', 'running', 'cancelling'):
        job['status'] = 'cancelled' if job['status'] == 'cancelling' else 'interrupted'
        write(job)


def store_image(job_id, filename, raw):
    directory = folder(job_id)
    directory.mkdir(exist_ok=True)
    path = directory / filename
    save_image(raw, path)
    data = path.read_bytes()
    if SERVERLESS and len(data) > 4_000_000:
        path.unlink()
        raise ValueError('Image is too large to serve; use an image under 4 MB')
    owner = read(job_id, internal=True).get('owner','legacy')
    with lock:
        if store.image_usage(owner) + len(data) > 100_000_000:
            path.unlink()
            raise ValueError('Workspace image storage limit of 100 MB reached')
        store.put_image(job_id, filename, data)
    path.unlink()


def process_job(job_id, batch_size=None):
    with lock:
        job = read(job_id, internal=True)
        if job['status'] in ('cancelled','cancelling'):
            job['status'] = 'cancelled'; write(job); return
        job['status'] = 'running'; write(job)
    processed = 0
    for index in range(len(job['products'])):
        job = read(job_id, internal=True)
        if job['status'] in ('cancelled','cancelling'):
            job['status'] = 'cancelled'; write(job); return
        product = job['products'][index]
        if product['status'] != 'pending': continue
        candidates = []
        errors = 0
        search_error = ''
        try:
            discovered = search_candidates(product['name'], product['quantity'])
        except SearchUnavailable as exc:
            discovered = []; search_error = str(exc)
        for candidate in discovered:
            try:
                raw = download_bytes(candidate.image_url)
                _, metrics = inspect_image(raw)
                for key, value in metrics.items(): setattr(candidate, key, value)
                candidate.text_score = textual_score(candidate, product['name'], product['quantity'])
                warnings = [candidate.reason] if candidate.reason else []
                if candidate.width < SETTINGS.min_width or candidate.height < SETTINGS.min_height:
                    warnings.append('Below preferred resolution; check image quality before export.')
                if candidate.white_ratio < SETTINGS.min_white_ratio:
                    warnings.append('Background is not predominantly white; check before approval.')
                candidate.reason = ' '.join(warnings)
                filename = f'{product["id"]}-{len(candidates)}.jpg'
                store_image(job_id, filename, raw)
                data = asdict(candidate)
                # No AI verification is performed in this first version.
                data.update(filename=filename, score=round(candidate.quality_score * .6 + candidate.text_score * .4, 1))
                candidates.append(data)
                if len(candidates) >= SETTINGS.max_candidates: break
            except Exception: errors += 1
        candidates.sort(key=lambda c: ('Pack size differs' in c['reason'] or 'Source pack size is unspecified' in c['reason'], bool(c['reason']), -c['score']))
        with lock:
            job = read(job_id, internal=True)
            if job['status'] in ('cancelled','cancelling'):
                job['status'] = 'cancelled'; write(job); return
            product = job['products'][index]
            reason = '' if candidates else (search_error or (
                f'Found {len(discovered)} image links, but none could be downloaded or decoded. Retry or upload an image.'
                if errors else 'No matching product image found. Check the product name and pack size, or upload an image.'))
            product.update(candidates=candidates, selected=0 if candidates else None, status='review', reason=reason)
            job['processed'] = sum(p['status'] != 'pending' for p in job['products'])
            write(job)
        processed += 1
        if batch_size and processed >= batch_size: break
    with lock:
        job = read(job_id, internal=True)
        if job['status'] not in ('cancelled','cancelling'):
            job['status'] = 'queued' if any(p['status'] == 'pending' for p in job['products']) else 'complete'; write(job)
        elif job['status'] == 'cancelling':
            job['status'] = 'cancelled'; write(job)

def run(job_id, batch_size=None):
    try:
        process_job(job_id, batch_size)
    except Exception:
        with lock:
            job = read(job_id, internal=True)
            if job['status'] not in ('cancelled','cancelling'):
                job['status'] = 'interrupted'
                write(job)
            elif job['status'] == 'cancelling':
                job['status'] = 'cancelled'; write(job)

@app.get('/api/jobs')
def jobs():
    with lock:
        return sorted(store.jobs(current_owner.get()), key=lambda j:j['created'], reverse=True)

@app.post('/api/jobs')
async def create(file: UploadFile):
    suffix = Path(file.filename or '').suffix.lower()
    if suffix not in ('.csv', '.xlsx'): raise HTTPException(400, 'Upload a CSV or XLSX file')
    limit = 4_000_000 if SERVERLESS else 10_000_000
    raw = await file.read(limit + 1)
    if len(raw) > limit: raise HTTPException(413, f'Maximum catalogue size is {limit // 1_000_000} MB')
    if len(store.jobs(current_owner.get())) >= 30:
        raise HTTPException(429, 'Limit of 30 catalogues reached. Export your existing work first.')
    job_id = uuid.uuid4().hex
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / ('catalogue' + suffix); path.write_bytes(raw)
        try: frame = load_products(path)
        except Exception as exc: raise HTTPException(400, str(exc))
    if not 0 < len(frame) <= 500: raise HTTPException(400, 'Upload between 1 and 500 unique products')
    import datetime
    job = {'owner':current_owner.get(),'id':job_id,'name':file.filename,'created':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'ready','processed':0,'products':[{'id':str(i),'name':r['Product Name'],'quantity':r['Quantity'],'status':'pending','candidates':[],'selected':None,'reason':''} for i,r in frame.iterrows()]}
    write(job); return job

@app.get('/api/jobs/{job_id}')
def get(job_id: str): return read(job_id)

@app.post('/api/jobs/{job_id}/start')
def start(job_id: str):
    with store.job_lock(job_id), lock:
        job = read(job_id)
        if job.get('archived') or job['status'] not in ('ready','interrupted','cancelled'): raise HTTPException(409,'This job has already started')
        if sum(j['status'] in ('queued','running') for j in store.jobs()) >= 20:
            raise HTTPException(429, 'Search queue is full. Try again shortly.')
        job['status']='queued'; write(job)
        if not SERVERLESS: pool.submit(run,job_id)
        return job

@app.post('/api/jobs/{job_id}/advance')
def advance(job_id: str):
    read(job_id)  # Authorize before acquiring a database lock.
    if not SERVERLESS: return read(job_id)
    with store.job_lock(job_id), lock:
        job = read(job_id)
        if job['status'] in ('queued', 'running', 'cancelling'):
            run(job_id, batch_size=1)
        return read(job_id)

@app.post('/api/jobs/{job_id}/cancel')
def cancel(job_id: str):
    with store.job_lock(job_id), lock:
        job = read(job_id)
        if job['status'] not in ('running', 'queued'): raise HTTPException(409, 'Search is not running')
        job['status'] = 'cancelled' if SERVERLESS else 'cancelling'; write(job)
        return job

@app.post('/api/jobs/{job_id}/retry')
def retry(job_id: str):
    with store.job_lock(job_id), lock:
        job = read(job_id)
        if job['status'] not in ('complete', 'interrupted', 'cancelled'):
            raise HTTPException(409, 'Wait for the current search to stop')
        for p in job['products']:
            if not p['candidates'] and p['status'] != 'approved': p['status'] = 'pending'
        job['processed'] = sum(p['status'] != 'pending' for p in job['products'])
        job['status'] = 'ready'; write(job)
        return job

@app.post('/api/jobs/{job_id}/archive')
def archive(job_id: str):
    with store.job_lock(job_id), lock:
        job = read(job_id)
        if job['status'] in ('running','queued','cancelling'): raise HTTPException(409, 'Pause the search before archiving')
        job['archived'] = not job.get('archived', False)
        write(job)
        return job

class Decision(BaseModel):
    action: str
    candidate: int | None = None

@app.post('/api/jobs/{job_id}/products/{product_id}')
def decide(job_id: str, product_id: str, decision: Decision):
    with store.job_lock(job_id), lock:
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
    limit = 4_000_000 if SERVERLESS else 15_000_000
    raw = await file.read(limit + 1)
    if len(raw)>limit: raise HTTPException(413,f'Maximum image size is {limit // 1_000_000} MB')
    with store.job_lock(job_id), lock:
        job=read(job_id)
        p=next((p for p in job['products'] if p['id']==product_id),None)
        if not p: raise HTTPException(404,'Product not found')
        if job['status'] in ('running','queued','cancelling'): raise HTTPException(409,'Pause search before replacing an image')
        filename=f'{product_id}-manual-{uuid.uuid4().hex}.jpg'
        try:
            _, metrics = inspect_image(raw)
            store_image(job_id, filename, raw)
        except Exception: raise HTTPException(400,'Upload a valid image')
        p['candidates'].append({'filename':filename,'score':None,'title':'Uploaded image','page_url':'','width':metrics['width'],'height':metrics['height'],'white_ratio':metrics['white_ratio'],'reason':'Manually supplied; not automatically verified'})
        p.update(selected=len(p['candidates'])-1,status='review',reason='')
        job['processed'] = sum(p['status'] != 'pending' for p in job['products'])
        write(job); return job

@app.get('/api/jobs/{job_id}/images/{filename}')
def image(job_id: str, filename: str):
    read(job_id)
    if Path(filename).name!=filename or not filename.endswith('.jpg'): raise HTTPException(404)
    raw = store.image(job_id, filename)
    if raw is None: raise HTTPException(404)
    return Response(raw, media_type='image/jpeg')

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
                    archive.writestr(name,store.image(job_id,name))
        mime='application/zip'
    else: raise HTTPException(404)
    return Response(stream.getvalue(),media_type=mime,headers={'Content-Disposition':f'attachment; filename="catalogue.{kind}"'})

if (ROOT/'frontend/dist').exists():
    app.mount('/',StaticFiles(directory=ROOT/'frontend/dist',html=True),name='frontend')
