'use client';

import type {ReactNode} from 'react';
import {createContext, useContext, useEffect, useMemo, useRef, useState} from 'react';

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
  const scrollYRef = useRef(0);

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

  // 滚动锁定逻辑
  useEffect(() => {
    if (open) {
      scrollYRef.current = window.scrollY;
      document.body.classList.add('overlay-open');
      document.body.style.top = `-${scrollYRef.current}px`;
    } else {
      document.body.classList.remove('overlay-open');
      document.body.style.top = '';
      window.scrollTo(0, scrollYRef.current);
    }

    return () => {
      document.body.classList.remove('overlay-open');
      document.body.style.top = '';
    };
  }, [open]);

  return <ContentViewerContext.Provider value={value}>{children}</ContentViewerContext.Provider>;
}

export function useContentViewer() {
  const ctx = useContext(ContentViewerContext);
  if (!ctx) throw new Error('useContentViewer must be used within ContentViewerProvider');
  return ctx;
}
