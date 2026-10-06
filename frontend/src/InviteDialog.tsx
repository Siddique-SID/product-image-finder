import {useState} from 'react';
import {Check, Copy, Mail, Send} from 'lucide-react';
import {Dialog} from './Dialog';
import {request} from './api';
type Invitation = {email:string;link:string;delivery:string;expires_days:number};
export function InviteDialog({onClose,online}:{onClose:()=>void;online:boolean}) {
  const [email,setEmail] = useState(''), [busy,setBusy] = useState(false), [error,setError] = useState(''), [result,setResult] = useState<Invitation|null>(null), [copied,setCopied] = useState(false);
  async function send(event:React.FormEvent) {
    event.preventDefault();setBusy(true);setError('');
    try {setResult(await request<Invitation>('/api/invites',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email})}));}
    catch (error) {setError((error as Error).message);} finally {setBusy(false);}
  }
  const sent = result?.delivery === 'sent';
  return <Dialog title="Invite by email" onClose={onClose} busy={busy}><div className={'dialog-icon '+(sent?'success-icon':'')} >{sent?<Check/>:<Mail/>}</div><div className="eyebrow">SHARE THE STUDIO</div><h2>{sent?'Invitation sent':'Invite by email'}</h2>
    {sent?<><p>We sent a signup link to <strong>{result.email}</strong>. They can open it, choose a password, and start their own private catalogue.</p><div className="success" role="status"><Check size={18}/> Email accepted by Gmail. Ask them to check Spam if it doesn’t arrive.</div><p className="muted">The link works once and expires in seven days.</p><div className="modal-actions"><button onClick={()=>{setResult(null);setEmail('');setCopied(false)}}>Invite another person</button><button className="primary" onClick={onClose}>Done</button></div></>:
    <><p>Enter their email address. We’ll send the invitation directly from Siddique’s Gmail account.</p><form onSubmit={send}><label>Recipient email<input type="email" autoComplete="email" placeholder="name@example.com" required maxLength={254} value={email} disabled={busy} onChange={event=>setEmail(event.target.value)}/></label>{error&&<div className="error" role="alert">{error}</div>}<button className="primary full" disabled={busy||!online}><Send size={16}/>{busy?'Sending email…':result?'Try sending again':'Send invitation'}</button></form>
    {result&&<div className="delivery-fallback" role="status"><strong>{result.delivery==='not_configured'?'Email sending isn’t configured on this deployment.':'Gmail could not send this invitation.'}</strong><p>Your private invitation link is ready. Retry sending, or use one of these options.</p><div className="inline-actions"><a className="button" href={'https://mail.google.com/mail/?view=cm&fs=1&to='+encodeURIComponent(result.email)+'&su='+encodeURIComponent('Your invitation to Product Image Finder')+'&body='+encodeURIComponent('Create your private workspace: '+result.link+'\n\nThis link is for '+result.email+' and expires in seven days.')} target="_blank" rel="noreferrer"><Mail size={16}/> Send from Gmail</a><button onClick={async()=>{try{await navigator.clipboard.writeText(result.link);setCopied(true)}catch{setError('Could not copy. Select and copy the link below.')}}}><Copy size={16}/>{copied?'Copied':'Copy link'}</button></div><input aria-label="Private invitation link" readOnly value={result.link} onFocus={event=>event.target.select()}/></div>}
    <p className="muted">One use · Valid for 7 days · Their catalogues stay private</p></>}
  </Dialog>;
}
