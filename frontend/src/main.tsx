import React, {useState, useEffect, useRef} from 'react';
import {createRoot} from 'react-dom/client';
import {Search, Upload, LayoutDashboard, Images, Check, CheckCheck, Download, WifiOff, X, ChevronRight, RefreshCw, FolderOpen, ArrowUpRight, Mail, LogOut, Archive, ImagePlus} from 'lucide-react';
import {registerSW} from 'virtual:pwa-register';
import {Account, type Session, type User} from './Account';
import {request, ApiError, messageFor, saveBlob} from './api';
import {Dialog} from './Dialog';
import {InviteDialog} from './InviteDialog';
import './style.css';
type Candidate = {filename:string;score:number|null;page_url:string;title:string;width:number;height:number;white_ratio:number;reason:string};
type Product = {id:string;name:string;quantity:string;status:string;candidates:Candidate[];selected:number|null;reason:string};
type Job = {archived?:boolean;id:string;name:string;created:string;status:string;processed:number;products:Product[]};
type InstallEvent = Event & {prompt:()=>Promise<void>};
type Page = 'Workspace'|'Catalogues'|'Review queue';
const running = (job?:Job) => !!job && ['queued','running','cancelling'].includes(job.status);
const statusLabel = (status:string) => ({ready:'Ready to search',queued:'Searching',running:'Searching',cancelling:'Pausing',complete:'Search complete',interrupted:'Search paused',cancelled:'Search paused',pending:'Not searched',review:'Needs review',approved:'Approved',rejected:'Rejected'}[status] || status);
function App() {
 const [jobs,setJobs]=useState<Job[]>([]), [active,setActive]=useState<string|null>(null), [page,setPage]=useState<Page>('Workspace');
 const [filter,setFilter]=useState('all'), [query,setQuery]=useState(''), [error,setError]=useState(''), [notice,setNotice]=useState(''), [busy,setBusy]=useState(false);
 const [online,setOnline]=useState(navigator.onLine), [authLoaded,setAuthLoaded]=useState(false), [user,setUser]=useState<User|null>(null), [durable,setDurable]=useState(false), [loadingJobs,setLoadingJobs]=useState(false);
 const [requestProcessing,setRequestProcessing]=useState(false), [uploadLimit,setUploadLimit]=useState(10_000_000), [review,setReview]=useState<string|null>(null);
 const [inviteOpen,setInviteOpen]=useState(false), [installHelp,setInstallHelp]=useState(false), [install,setInstall]=useState<InstallEvent|null>(null), [update,setUpdate]=useState(false), [recovery,setRecovery]=useState('');
 const input=useRef<HTMLInputElement>(null), replacement=useRef<HTMLInputElement>(null), mutation=useRef(false), revision=useRef(0);
 const job=jobs.find(item=>item.id===active), product=job?.products.find(item=>item.id===review);
 const liveJobs=jobs.filter(item=>!item.archived), reviewCount=liveJobs.reduce((total,item)=>total+item.products.filter(p=>p.status==='review').length,0);
 const incomingInvitation=new URLSearchParams(window.location.hash.slice(1)).has('invite');
 const all=job?.products||[], approved=all.filter(p=>p.status==='approved').length, needs=all.filter(p=>p.status==='review').length;
 const visible=all.filter(p=>(filter==='all'||p.status===filter)&&(page!=='Review queue'||p.status==='review')&&`${p.name} ${p.quantity}`.toLowerCase().includes(query.toLowerCase()));
 function acceptSession(data:Session) {revision.current++;setUser(data.user);setDurable(data.durable);setRequestProcessing(!!data.request_processing);setUploadLimit(data.upload_limit||10_000_000);setAuthLoaded(true);setJobs([]);setActive(null);setReview(null);setError('');if(data.recovery_code)setRecovery(data.recovery_code);}
 function expired(error:unknown) {if(error instanceof ApiError&&error.status===401){setUser(null);setJobs([]);setActive(null);setReview(null);}setError((error as Error).message);}
 async function session() {try {acceptSession(await request<Session>('/api/session'));}catch {setAuthLoaded(true);setError('Unable to connect. Please retry.');}}
 useEffect(()=>{localStorage.removeItem('catalogue-jobs');session();},[]);
 useEffect(()=>{
  const network=()=>setOnline(navigator.onLine), prompt=(event:Event)=>{event.preventDefault();setInstall(event as InstallEvent);};
  window.addEventListener('online',network);window.addEventListener('offline',network);window.addEventListener('beforeinstallprompt',prompt);
  let registration:ServiceWorkerRegistration|undefined;
  registerSW({onNeedRefresh(){setUpdate(true)},onRegisteredSW(_,sw){registration=sw;}});
  const check=()=>{if(navigator.onLine)registration?.update().catch(()=>{});};
  const interval=setInterval(check,60000);window.addEventListener('focus',check);
  return()=>{clearInterval(interval);window.removeEventListener('focus',check);window.removeEventListener('online',network);window.removeEventListener('offline',network);window.removeEventListener('beforeinstallprompt',prompt);};
 },[]);
 useEffect(()=>{
  if(!user)return;let alive=true,refreshing=false;setLoadingJobs(true);
  async function refresh() {
   if(refreshing||mutation.current||!navigator.onLine)return;refreshing=true;const version=revision.current;
   try {
    const data=await request<Job[]>('/api/jobs');if(!alive||version!==revision.current)return;setJobs(data);setLoadingJobs(false);
    const next=data.find(item=>running(item));
    if(requestProcessing&&next&&!mutation.current){
     try {const updated=await request<Job>(`/api/jobs/${next.id}/advance`,{method:'POST'});if(alive&&version===revision.current)setJobs(prev=>prev.map(item=>item.id===updated.id?updated:item));}
     catch(error){if(!(error instanceof ApiError&&error.status===409))throw error;}
    }
   } catch(error) {if(alive){setLoadingJobs(false);expired(error);}} finally {refreshing=false;}
  }
  refresh();const interval=setInterval(refresh,3000);
  return()=>{alive=false;clearInterval(interval);};
 },[user?.id,requestProcessing,online]);
 function openJob(id:string,nextPage:Page='Workspace') {setActive(id);setPage(nextPage);setFilter('all');setQuery('');setError('');setNotice('');setReview(null);}
 function navigate(next:Page) {
  setPage(next);setFilter('all');setQuery('');setError('');setNotice('');setReview(null);
  if(next==='Review queue'&&(!job||job.archived||!job.products.some(p=>p.status==='review'))){setActive(liveJobs.find(item=>item.products.some(p=>p.status==='review'))?.id||null);}
 }
 async function mutate(url:string,options:RequestInit):Promise<Job|undefined> {
  if(!online){setError('Reconnect to make changes.');return;}if(mutation.current)return;
  mutation.current=true;revision.current++;setBusy(true);setError('');setNotice('');
  try {const data=await request<Job>(url,options);setJobs(prev=>[data,...prev.filter(item=>item.id!==data.id)].sort((a,b)=>b.created.localeCompare(a.created)));return data;}
  catch(error){expired(error);}finally {mutation.current=false;setBusy(false);}
 }
 async function upload(file?:File) {
  if(!file)return;
  if(!/\.(csv|xlsx)$/i.test(file.name)){setError('Choose a CSV or Excel (.xlsx) file.');return;}
  if(file.size>uploadLimit){setError(`Choose a catalogue under ${uploadLimit/1_000_000} MB.`);return;}
  const body=new FormData();body.append('file',file);const result=await mutate('/api/jobs',{method:'POST',body});
  if(result){openJob(result.id);setNotice('Catalogue uploaded. Choose Find images to start, or open a product to upload your own image.');}
  if(input.current)input.current.value='';
 }
 async function decide(action:string,candidate?:number) {
  if(!job||!product)return;
  return mutate(`/api/jobs/${job.id}/products/${product.id}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,candidate})});
 }
 async function download(kind:'csv'|'xlsx'|'zip') {
  if(!job||busy)return;setBusy(true);setError('');setNotice('');
  try {
   if(kind==='zip') {
    const {zipSync}=await import('fflate');const files:Record<string,Uint8Array>={};
    for(const p of job.products){if(p.status!=='approved'||p.selected===null)continue;const name=p.candidates[p.selected].filename;const response=await fetch(`/api/jobs/${job.id}/images/${name}`);if(!response.ok)throw new ApiError(messageFor(await response.json().catch(()=>null),'Could not download an image. Please retry.'),response.status);files[name]=new Uint8Array(await response.arrayBuffer());}
    if(!Object.keys(files).length)throw Error('Approve at least one image before downloading.');
    saveBlob(new Blob([new Uint8Array(zipSync(files,{level:0})).buffer],{type:'application/zip'}),`${job.name.replace(/\.[^.]+$/,'')}-images.zip`);
   } else {
    const response=await fetch(`/api/jobs/${job.id}/export/${kind}`);if(!response.ok)throw new ApiError(messageFor(await response.json().catch(()=>null),'Could not download the report. Please retry.'),response.status);
    saveBlob(await response.blob(),`${job.name.replace(/\.[^.]+$/,'')}-report.${kind}`);
   }
   setNotice('Download ready. Check your browser’s downloads.');
  }catch(error){expired(error);}finally{setBusy(false);}
 }
 async function signOut(){if(busy)return;try{await request('/api/logout',{method:'POST'});acceptSession({authenticated:false,user:null,durable,registration:true});setRecovery('');}catch(error){expired(error);}}
 const initials=user?.id==='legacy'?'SS':(user?.name||'U').split(' ').map(word=>word[0]).slice(0,2).join('').toUpperCase();
 if(!authLoaded)return <div className="boot-screen"><img src="/brand-logo.png" alt="Product Image Finder"/><p>Opening your workspace…</p></div>;
 if(!user)return <><Account onSession={acceptSession}/>{error&&<div className="connection-error" role="alert">{error}<button onClick={session}>Retry connection</button></div>}</>;
 return <div className="app"><aside><div className="brand"><img src="/brand-logo.png" alt="Product Image Finder"/></div><div className="nav-label">YOUR WORKSPACE</div><nav>{([['Workspace',LayoutDashboard],['Catalogues',FolderOpen],['Review queue',Images]] as const).map(([name,Icon])=><button key={name} className={page===name?'selected':''} onClick={()=>navigate(name)}><Icon size={19}/>{name}{name==='Review queue'&&reviewCount>0&&<b>{reviewCount}</b>}</button>)}</nav><div className="aside-note"><span className="beta-pill">FREE HOBBY STUDIO</span><strong>Good images.<br/>Better catalogues.</strong><p>A private place to find, review and organise your product images.</p></div><footer><div className="avatar">{initials}</div><div>{user.name}<small>{user.id==='legacy'?'Owner account':'Private account'}</small></div></footer></aside>
 <main><header><span className="breadcrumb">Studio <ChevronRight size={14}/>{page}</span><div className="header-actions"><button className="install-button" onClick={async()=>{if(install){try{await install.prompt();setInstall(null);}catch{setInstallHelp(true);}}else setInstallHelp(true);}}><Download size={16}/>Install app</button>{user.id==='legacy'&&<button onClick={()=>setInviteOpen(true)}><Mail size={16}/>Invite by email</button>}<button className="signout" disabled={busy} aria-label="Sign out" title="Sign out" onClick={signOut}><LogOut size={17}/><span>Sign out</span></button><div className="avatar">{initials}</div></div></header>
 {incomingInvitation&&<div className="notice">You opened an invitation while already signed in. Sign out to create the invited account.<button disabled={busy} onClick={signOut}>Use invitation</button></div>}
 {!online&&<div className="notice"><WifiOff size={18}/> You’re offline. Reconnect to load catalogues and make changes.</div>}{update&&<div className="notice">An app update is ready.<button disabled={busy||running(job)} onClick={()=>window.location.assign('/api/app-update')}>Update app</button>{running(job)&&<span>Pause your search first.</span>}</div>}
 {error&&!product&&<div className="error" role="alert">{error}<button aria-label="Dismiss error" onClick={()=>setError('')}><X size={16}/></button></div>}{notice&&<div className="success" role="status"><Check size={17}/>{notice}<button aria-label="Dismiss message" onClick={()=>setNotice('')}><X size={16}/></button></div>}
 {!durable&&<div className="notice">Local demo storage. Export your work before restarting the server.</div>}
 <section className="page-heading"><div><div className="eyebrow">PRODUCT IMAGE FINDER</div><h1>{page==='Catalogues'?'Your catalogues':page==='Review queue'?'Ready for your review':job?'Your product library':'Build a better catalogue'}</h1><p>{page==='Catalogues'?'Pick up where you left off, or upload a new product list.':page==='Review queue'?'Check the product, pack size and image before approving.':job?'Find images, check each match, then download your approved catalogue.':'Upload your product list. Find the images. Make them yours.'}</p></div><button className="primary" disabled={busy||!online} onClick={()=>input.current?.click()}><Upload size={17}/> Upload catalogue</button></section>
 <input ref={input} type="file" accept=".csv,.xlsx" hidden onChange={event=>upload(event.target.files?.[0])}/>
 {page==='Catalogues'?<section className="catalogues"><div className="section-title"><h2>All catalogues</h2><span>{jobs.length} saved</span></div>{loadingJobs&&!jobs.length?<div className="empty"><RefreshCw className="spin"/><h3>Loading catalogues…</h3></div>:!jobs.length?<div className="empty"><FolderOpen/><h3>Your first catalogue starts here</h3><p>Upload a CSV or Excel file to begin.</p></div>:jobs.map(item=><button className="job-row" key={item.id} onClick={()=>openJob(item.id)}><div className="file-icon"><FolderOpen/></div><div><strong>{item.name}</strong><small>{new Date(item.created).toLocaleDateString()} · {item.products.length} products · {item.products.filter(p=>p.status==='approved').length} approved</small></div><span className={'badge '+item.status}>{item.archived?'Archived':statusLabel(item.status)}</span><ChevronRight size={18}/></button>)}</section>:<>
 {job?<>
 <section className="catalogue-head"><div className="file-icon"><FolderOpen size={22}/></div><div className="catalogue-title"><h2>{job.name}</h2><p>{all.length} products · {job.archived?'Archived':statusLabel(job.status)}</p></div><select aria-label="Select catalogue" value={active||''} onChange={event=>openJob(event.target.value,page)}>{(page==='Review queue'?liveJobs:jobs).map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select>
 {job.archived?<button className="primary" disabled={busy||!online} onClick={()=>mutate(`/api/jobs/${job.id}/archive`,{method:'POST'})}>Restore catalogue</button>:<>
 {['ready','interrupted','cancelled'].includes(job.status)&&<button className="primary" disabled={busy||!online||!all.some(p=>p.status==='pending')} onClick={()=>mutate(`/api/jobs/${job.id}/start`,{method:'POST'})}><Search size={17}/>{job.status==='ready'?'Find images':'Resume search'}</button>}
 {['running','queued'].includes(job.status)&&<button disabled={busy||!online} onClick={()=>mutate(`/api/jobs/${job.id}/cancel`,{method:'POST'})}>Pause search</button>}
 {!running(job)&&<button className="icon-button" aria-label="Archive catalogue" title="Archive catalogue" disabled={busy||!online} onClick={async()=>{if(await mutate(`/api/jobs/${job.id}/archive`,{method:'POST'})){setActive(null);navigate('Catalogues');}}}><Archive size={18}/></button>}
 {['complete','interrupted','cancelled'].includes(job.status)&&all.some(p=>!p.candidates.length&&p.status!=='approved')&&<button disabled={busy||!online} onClick={()=>mutate(`/api/jobs/${job.id}/retry`,{method:'POST'})}><RefreshCw size={16}/>Retry missing images</button>}
 </>}
 </section>
 {job.archived&&<div className="notice">This catalogue is archived. You can download it, or restore it to continue editing.</div>}
 <div className="workflow-steps"><span className="done"><Check size={14}/>1. Upload</span><ChevronRight size={14}/><span className={running(job)?'current':job.status==='ready'?'':'done'}>2. Find images</span><ChevronRight size={14}/><span className={needs?'current':approved?'done':''}>3. Review</span><ChevronRight size={14}/><span>4. Download</span></div>
 <div className="stats"><div><span><Images size={17}/> Products</span><strong>{all.length}</strong><small>In this catalogue</small></div><div><span><Search size={17}/> Needs review</span><strong>{needs}</strong><small>Check and approve each match</small></div><div><span><CheckCheck size={17}/> Approved</span><strong>{approved}</strong><small>Ready to download</small></div></div>
 {running(job)&&<div className="progress" role="status"><div><RefreshCw size={16} className="spin"/>{job.status==='cancelling'?'Pausing search…':'Finding product images'}<span>{job.processed} / {all.length}</span></div><progress max={all.length} value={job.processed}/>{requestProcessing&&<p>Keep this app open while searching. Your progress is saved. If a product is taking a while, pause becomes available when it finishes.</p>}</div>}
 <section className="library"><div className="section-title"><div><h2>{page==='Review queue'?'Review queue':'Product library'}</h2><p className="muted">Click a product to review its images or add your own.</p></div><div className="exports"><button disabled={!online||busy} onClick={()=>download('csv')}><Download size={15}/> CSV report</button><button disabled={!online||busy} onClick={()=>download('xlsx')}><Download size={15}/> Excel report</button><button className="primary" disabled={!online||busy||!approved} title={!approved?'Approve an image first':'Download approved images as ZIP'} onClick={()=>download('zip')}><Download size={15}/> Images ({approved})</button></div></div>
 <div className="toolbar"><div className="filters">{(page==='Review queue'?['review']:['all','review','approved','rejected','pending']).map(value=><button key={value} className={(page==='Review queue'||filter===value)?'current':''} onClick={()=>setFilter(value)}>{value==='all'?'All products':statusLabel(value)}</button>)}</div><label className="search"><Search size={16}/><input aria-label="Search products" placeholder="Search products…" value={query} onChange={event=>setQuery(event.target.value)}/>{query&&<button className="icon-button" aria-label="Clear search" onClick={()=>setQuery('')}><X size={14}/></button>}</label></div>
 <div className="grid">{visible.map(p=>{const candidate=p.selected===null?null:p.candidates[p.selected];return <button key={p.id} className="product-card" onClick={()=>{setError('');setReview(p.id);}}><div className="image-area">{candidate?<img loading="lazy" src={`/api/jobs/${job.id}/images/${candidate.filename}`} alt={p.name}/>:<div className="image-placeholder"><ImagePlus size={32}/><span>{p.status==='pending'?(running(job)?'Waiting for search':'Find images or add your own'):'No image found · Add your own'}</span></div>}<span className={'badge '+p.status}>{statusLabel(p.status)}</span>{candidate?.score!=null&&<span className="score">{candidate.score}<small>match score</small></span>}</div><div className="product-info"><h3>{p.name}</h3><p>{p.quantity}<span>{candidate?`${candidate.width} × ${candidate.height}`:`${p.candidates.length} images`}</span></p>{(p.reason||candidate?.reason)&&<small className="image-warning">{p.reason||candidate?.reason}</small>}<span className="card-action">{candidate?'Review image':'Add an image'}<ArrowUpRight size={14}/></span></div></button>;})}</div>
 {!visible.length&&<div className="empty"><CheckCheck/><h3>{query?'No matching products':page==='Review queue'?'This catalogue is reviewed':'No products in this view'}</h3><p>{query?'Try a different name or pack size.':running(job)?'Images will appear as the search progresses.':'Choose another filter or catalogue.'}</p>{(query||filter!=='all')&&page!=='Review queue'&&<button onClick={()=>{setQuery('');setFilter('all');}}>Show all products</button>}{page==='Review queue'&&<button onClick={()=>navigate('Catalogues')}>Choose another catalogue</button>}</div>}
 </section>
 </>:page==='Review queue'?<div className="empty panel"><CheckCheck size={32}/><h2>{loadingJobs?'Loading your review queue…':'You’re all caught up'}</h2><p>Image matches will appear here after you search a catalogue.</p><button onClick={()=>navigate('Catalogues')}>View catalogues</button></div>:<>
 <div className="onboarding"><div className="upload-zone" onDragOver={event=>event.preventDefault()} onDrop={event=>{event.preventDefault();upload(event.dataTransfer.files[0]);}}><div className="upload-icon"><Upload size={28}/></div><h2>Bring your product list</h2><p>Drop a CSV or Excel file here.<br/>We’ll find the product names and pack sizes.</p><button className="primary" disabled={busy||!online} onClick={()=>input.current?.click()}>{busy?'Uploading…':'Choose a file'}</button><small>CSV or XLSX · Up to 500 products · {uploadLimit/1_000_000} MB</small></div><div className="how"><div className="eyebrow">FOUR SIMPLE STEPS</div>{[['01','Upload your list','Product names and pack sizes, in CSV or Excel.'],['02','Find matching images','Start the search and keep this app open.'],['03','Review every match','Check the image, or upload your own.'],['04','Download your catalogue','Get approved images and CSV or Excel reports.']].map(([number,title,description])=><div key={number}><span>{number}</span><section><h3>{title}</h3><p>{description}</p></section></div>)}<a href="/sample.csv" download><Download size={15}/> Download an example CSV</a></div></div>
 {liveJobs.length>0&&<section className="recent"><div className="section-title"><h2>Continue a catalogue</h2><button onClick={()=>navigate('Catalogues')}>View all <ChevronRight size={15}/></button></div>{liveJobs.slice(0,3).map(item=><button className="job-row" key={item.id} onClick={()=>openJob(item.id)}><div className="file-icon"><FolderOpen size={20}/></div><div><strong>{item.name}</strong><small>{item.products.length} products · {item.products.filter(p=>p.status==='review').length} to review</small></div><span className="badge">{statusLabel(item.status)}</span><ChevronRight size={18}/></button>)}</section>}
 </>}
 </>}
 <div className="credit">Made by <strong>Siddique Sayed</strong><span>Product Image Finder · Private workspace</span></div></main>
 {inviteOpen&&<InviteDialog onClose={()=>setInviteOpen(false)} online={online}/>}
 {recovery&&<Dialog title="Save recovery code" dismissible={false} onClose={()=>{}}><h2>Keep your recovery code safe</h2><p>You’ll need this code to reset your password. It’s shown only once.</p><code className="recovery-code">{recovery}</code><div className="modal-actions"><button onClick={()=>saveBlob(new Blob([recovery],{type:'text/plain'}),'studio-recovery-code.txt')}>Download code</button><button className="primary" onClick={()=>setRecovery('')}>I’ve saved my code</button></div></Dialog>}
 {installHelp&&<Dialog title="Install the app" onClose={()=>setInstallHelp(false)}><h2>Your studio, on your home screen</h2><p>Installation is free. Searching and catalogues require internet access.</p><div className="install-steps"><p><strong>iPhone / iPad</strong>Open in Safari → Share → Add to Home Screen.</p><p><strong>Android</strong>Open in Chrome → Menu → Install app.</p><p><strong>Desktop</strong>Use Chrome or Edge → Install icon in the address bar.</p></div><button className="primary" onClick={()=>setInstallHelp(false)}>Got it</button></Dialog>}
 {product&&job&&<Dialog title={'Review '+product.name} onClose={()=>{setReview(null);setError('');}} busy={busy} wide><div className="eyebrow">PRODUCT REVIEW</div><h2>{product.name}</h2><p>{product.quantity} · {statusLabel(product.status)}</p>{error&&<div className="error" role="alert">{error}</div>}<div className="review-image">{product.selected!==null?<img src={`/api/jobs/${job.id}/images/${product.candidates[product.selected].filename}`} alt={product.name}/>:<div className="image-placeholder"><ImagePlus size={42}/><span>{running(job)?'Search in progress':'No image selected'}</span></div>}</div>
 {product.reason&&<p className="image-warning">{product.reason}</p>}<div className="candidates">{product.candidates.map((candidate,index)=><button key={candidate.filename} aria-label={`Select candidate ${index+1}`} className={product.selected===index?'chosen':''} disabled={busy||!online||job.archived} onClick={()=>decide('select',index)}><img src={`/api/jobs/${job.id}/images/${candidate.filename}`} alt={`Candidate ${index+1}`}/><span>{candidate.score??'Uploaded'}</span></button>)}</div>
 {product.selected!==null&&<><div className="details"><span>Resolution <b>{product.candidates[product.selected].width} × {product.candidates[product.selected].height}</b></span>{product.candidates[product.selected].page_url&&<a href={product.candidates[product.selected].page_url} target="_blank" rel="noreferrer">Original source <ArrowUpRight size={15}/></a>}</div>{product.candidates[product.selected].reason&&<p className="image-warning">{product.candidates[product.selected].reason}</p>}</>}
 <p className="review-note">Check the exact product, variant and pack size. Approve only images you have permission to use.</p>{running(job)&&<p className="muted">Pause the search before uploading your own image.</p>}{job.archived&&<p className="muted">Restore this catalogue to make changes.</p>}
 <input ref={replacement} type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={async event=>{const file=event.target.files?.[0];if(file){const body=new FormData();body.append('file',file);await mutate(`/api/jobs/${job.id}/products/${product.id}/image`,{method:'POST',body});if(replacement.current)replacement.current.value='';}}}/>
 <div className="modal-actions"><button disabled={busy||!online||running(job)||job.archived} onClick={()=>replacement.current?.click()}><Upload size={16}/>{product.candidates.length?'Upload replacement':'Upload image'}</button><button disabled={busy||!online||product.status==='pending'||job.archived} onClick={async()=>{if(await decide('reject'))setReview(null);}}>Reject</button><button className="primary" disabled={busy||!online||product.selected===null||job.archived} onClick={async()=>{if(await decide('approve'))setReview(null);}}><Check size={17}/>Approve image</button></div>
 </Dialog>}
 </div>;
}
createRoot(document.getElementById('root')!).render(<App/>);
