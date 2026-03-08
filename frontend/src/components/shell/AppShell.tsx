'use client';

import type {ReactNode} from 'react';
import {usePathname} from '@/i18n/navigation';
import {isPublicPath} from '@src/lib/utils/paths';
import {ProtectedAppShell} from './ProtectedAppShell';

function AppShell({children}: {children: ReactNode}) {
  const pathname = usePathname();
  if (isPublicPath(pathname)) return <>{children}</>;
  return <ProtectedAppShell>{children}</ProtectedAppShell>;
}

export {AppShell};
export default AppShell;
