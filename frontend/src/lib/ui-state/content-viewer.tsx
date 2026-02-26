'use client';

import type {ReactNode} from 'react';
import {createContext, useContext, useMemo, useState} from 'react';

interface ContentViewerContextValue {
  open: boolean;
  docId: string;
  openViewer: (docId: string) => void;
  closeViewer: () => void;
}

const ContentViewerContext = createContext<ContentViewerContextValue | null>(null);

export function ContentViewerProvider({children}: {children: ReactNode}) {
  const [open, setOpen] = useState(false);
  const [docId, setDocId] = useState('');

  const value = useMemo(
    () => ({
      open,
      docId,
      openViewer: (nextDocId: string) => {
        const safe = String(nextDocId || '').trim();
        if (!safe) return;
        setDocId(safe);
        setOpen(true);
      },
      closeViewer: () => {
        setOpen(false);
      }
    }),
    [docId, open]
  );

  return <ContentViewerContext.Provider value={value}>{children}</ContentViewerContext.Provider>;
}

export function useContentViewer() {
  const ctx = useContext(ContentViewerContext);
  if (!ctx) throw new Error('useContentViewer must be used within ContentViewerProvider');
  return ctx;
}
