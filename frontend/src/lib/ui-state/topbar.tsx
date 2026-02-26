'use client';

import type {ReactNode} from 'react';
import {createContext, useContext, useMemo, useState} from 'react';

export interface TopbarState {
  title: string;
  metaMode: 'count' | 'text' | 'locale_switch';
  count: number;
  metaText: string;
}

interface TopbarContextValue {
  state: TopbarState;
  setTopbar: (next: TopbarState) => void;
}

const TopbarContext = createContext<TopbarContextValue | null>(null);

export function TopbarProvider({children}: {children: ReactNode}) {
  const [state, setTopbar] = useState<TopbarState>({
    title: '',
    metaMode: 'count',
    count: 0,
    metaText: ''
  });

  const value = useMemo(() => ({state, setTopbar}), [state]);

  return <TopbarContext.Provider value={value}>{children}</TopbarContext.Provider>;
}

export function useTopbar() {
  const ctx = useContext(TopbarContext);
  if (!ctx) throw new Error('useTopbar must be used within TopbarProvider');
  return ctx;
}
