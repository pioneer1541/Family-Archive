'use client';

import type {ReactNode} from 'react';
import dynamic from 'next/dynamic';
import {usePathname} from '@/i18n/navigation';

const ProtectedAppShell = dynamic(() => import('./ProtectedAppShell').then((mod) => mod.ProtectedAppShell));
const PUBLIC_PATHS = ['/setup', '/login', '/register'];

function normalizePath(pathname: string): string {
  return String(pathname || '').replace(/^\/(zh-CN|en-AU)(?=\/|$)/, '') || '/';
}

function isPublicPath(pathname: string): boolean {
  const normalized = normalizePath(pathname);
  return PUBLIC_PATHS.some((path) => normalized === path || normalized.startsWith(path + '/'));
}

function AppShell({children}: {children: ReactNode}) {
  const pathname = usePathname();
  if (isPublicPath(pathname)) return <>{children}</>;
  return <ProtectedAppShell>{children}</ProtectedAppShell>;
}

export {AppShell};
export default AppShell;
