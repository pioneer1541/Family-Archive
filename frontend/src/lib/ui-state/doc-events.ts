import type {KbDoc} from '@src/lib/api/types';

const DOC_UPDATED_EVENT = 'fkv:doc-updated';

export function emitDocUpdated(doc: KbDoc): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent<KbDoc>(DOC_UPDATED_EVENT, {detail: doc}));
}

export function subscribeDocUpdated(handler: (doc: KbDoc) => void): () => void {
  if (typeof window === 'undefined') return () => {};
  const listener = (event: Event) => {
    const custom = event as CustomEvent<KbDoc>;
    if (!custom.detail) return;
    handler(custom.detail);
  };
  window.addEventListener(DOC_UPDATED_EVENT, listener as EventListener);
  return () => {
    window.removeEventListener(DOC_UPDATED_EVENT, listener as EventListener);
  };
}
