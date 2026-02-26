'use client';

import type {ReactNode} from 'react';
import {createContext, useCallback, useContext, useMemo, useRef, useState} from 'react';

interface ToastContextValue {
  message: string;
  visible: boolean;
  showToast: (msg: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({children}: {children: ReactNode}) {
  const [message, setMessage] = useState('');
  const [visible, setVisible] = useState(false);
  const timerRef = useRef<number | null>(null);

  const showToast = useCallback((msg: string) => {
    const safe = String(msg || '').trim();
    if (!safe) return;

    if (timerRef.current) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }

    setMessage(`✓ ${safe}`);
    setVisible(true);

    timerRef.current = window.setTimeout(() => {
      setVisible(false);
    }, 2200);
  }, []);

  const value = useMemo(() => ({message, visible, showToast}), [message, visible, showToast]);

  return <ToastContext.Provider value={value}>{children}</ToastContext.Provider>;
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    throw new Error('useToast must be used within ToastProvider');
  }
  return ctx;
}
