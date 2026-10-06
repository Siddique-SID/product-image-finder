import {useEffect, useRef, type ReactNode} from 'react';
import {X} from 'lucide-react';
export function Dialog({title, children, onClose, busy = false, wide = false, dismissible = true}: {title:string;children:ReactNode;onClose:()=>void;busy?:boolean;wide?:boolean;dismissible?:boolean}) {
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement;
    const first = ref.current?.querySelector<HTMLElement>('input:not([hidden]):not(:disabled)') || ref.current?.querySelector<HTMLElement>('button:not(:disabled),a[href]'); first?.focus();
    const handler = (event:KeyboardEvent) => {
      if (event.key === 'Escape' && !busy && dismissible) onClose();
      if (event.key !== 'Tab') return;
      const nodes = Array.from(ref.current?.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],input:not([hidden]):not(:disabled)') || []);
      const first = nodes[0], last = nodes.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener('keydown', handler);
    return () => {document.removeEventListener('keydown', handler); previous?.focus();};
  }, [busy, dismissible]);
  return <div className="modal-backdrop" onClick={() => !busy && dismissible && onClose()}><section ref={ref} className={'modal ' + (wide ? 'wide' : '')} role="dialog" aria-modal="true" aria-label={title} onClick={event => event.stopPropagation()}>{dismissible&&<button className="close" disabled={busy} aria-label={'Close '+title} onClick={onClose}><X size={20}/></button>}{children}</section></div>;
}
