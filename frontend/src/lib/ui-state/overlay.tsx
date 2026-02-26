'use client';

import type {ReactNode} from 'react';
import {createContext, useContext, useMemo, useState} from 'react';

interface OverlayContextValue {
  open: boolean;
  docId: string;
  openOverlay: (docId: string) => void;
  closeOverlay: () => void;
}

const OverlayContext = createContext<OverlayContextValue | null>(null);

export function OverlayProvider({children}: {children: ReactNode}) {
  const [open, setOpen] = useState(false);
  const [docId, setDocId] = useState('');

  const value = useMemo(
    () => ({
      open,
      docId,
      openOverlay: (nextDocId: string) => {
        const safe = String(nextDocId || '').trim();
        if (!safe) return;
        setDocId(safe);
        setOpen(true);
      },
      closeOverlay: () => {
        setOpen(false);
      }
    }),
    [open, docId]
  );

  return <OverlayContext.Provider value={value}>{children}</OverlayContext.Provider>;
}

export function useOverlay() {
  const ctx = useContext(OverlayContext);
  if (!ctx) throw new Error('useOverlay must be used within OverlayProvider');
  return ctx;
}
