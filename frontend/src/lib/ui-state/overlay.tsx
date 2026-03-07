'use client';

import type {ReactNode} from 'react';
import {createContext, useContext, useEffect, useMemo, useState, useRef} from 'react';

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
  const scrollYRef = useRef(0);

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

  // 滚动锁定逻辑
  useEffect(() => {
    if (open) {
      // 记录当前滚动位置
      scrollYRef.current = window.scrollY;
      // 锁定滚动
      document.body.classList.add('overlay-open');
      document.body.style.top = `-${scrollYRef.current}px`;
    } else {
      // 恢复滚动
      document.body.classList.remove('overlay-open');
      document.body.style.top = '';
      window.scrollTo(0, scrollYRef.current);
    }

    return () => {
      document.body.classList.remove('overlay-open');
      document.body.style.top = '';
    };
  }, [open]);

  return <OverlayContext.Provider value={value}>{children}</OverlayContext.Provider>;
}

export function useOverlay() {
  const ctx = useContext(OverlayContext);
  if (!ctx) throw new Error('useOverlay must be used within OverlayProvider');
  return ctx;
}
