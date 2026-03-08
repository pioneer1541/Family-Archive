'use client';

import type {ReactNode} from 'react';
import dynamic from 'next/dynamic';
import {usePathname} from '@/i18n/navigation';
import {isPublicPath} from '@src/lib/utils/paths';

const ProtectedAppShell = dynamic(() => import('./ProtectedAppShell').then((mod) => mod.ProtectedAppShell));

function AppShell({children}: {children: ReactNode}) {
  const pathname = usePathname();
  if (isPublicPath(pathname)) return <>{children}</>;
  return <ProtectedAppShell>{children}</ProtectedAppShell>;
}

export {AppShell};
export default AppShell;
