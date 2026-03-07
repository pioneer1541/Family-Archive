'use client';

import type {ReactNode} from 'react';
import {createContext, useCallback, useContext, useEffect, useMemo, useRef, useState} from 'react';

interface SyncViewerState {
  open: boolean;
  runId: string;
}

interface SyncViewerContextValue extends SyncViewerState {
  openSyncViewer: (runId: string) => void;
  closeSyncViewer: () => void;
}

const SyncViewerContext = createContext<SyncViewerContextValue | null>(null);

export function SyncViewerProvider({children}: {children: ReactNode}) {
  const [state, setState] = useState<SyncViewerState>({open: false, runId: ''});
  const scrollYRef = useRef(0);

  const openSyncViewer = useCallback((runId: string) => {
    const safe = String(runId || '').trim();
    if (!safe) return;
    setState({open: true, runId: safe});
  }, []);

  const closeSyncViewer = useCallback(() => {
    setState((prev) => ({...prev, open: false}));
  }, []);

  // 滚动锁定逻辑
  useEffect(() => {
    if (state.open) {
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
  }, [state.open]);

  const value = useMemo(
    () => ({open: state.open, runId: state.runId, openSyncViewer, closeSyncViewer}),
    [closeSyncViewer, openSyncViewer, state.open, state.runId]
  );

  return <SyncViewerContext.Provider value={value}>{children}</SyncViewerContext.Provider>;
}

export function useSyncViewer() {
  const ctx = useContext(SyncViewerContext);
  if (!ctx) throw new Error('useSyncViewer must be used within SyncViewerProvider');
  return ctx;
}
