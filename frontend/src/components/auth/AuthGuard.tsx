'use client';

import {useEffect, useState} from 'react';
import {useRouter, usePathname} from 'next/navigation';
import {getKbClient} from '@src/lib/api/kb-client';

// Routes that don't require authentication
const PUBLIC_PATHS = ['/setup', '/login'];

function isPublicPath(pathname: string): boolean {
  // Strip locale prefix
  const stripped = String(pathname || '').replace(/^\/(zh-CN|en-AU)(?=\/|$)/, '') || '/';
  return PUBLIC_PATHS.some((p) => stripped === p || stripped.startsWith(p + '/'));
}

export function AuthGuard({children}: {children: React.ReactNode}) {
  const router = useRouter();
  const pathname = usePathname();
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    if (isPublicPath(pathname)) {
      setChecked(true);
      return;
    }

    const client = getKbClient();
    if (!client.getAuthStatus) {
      // Mock adapter — no auth required
      setChecked(true);
      return;
    }

    client.getAuthStatus().then((status) => {
      if (!status.setup_complete) {
        // Derive locale from current path
        const locale = pathname.startsWith('/en-AU') ? 'en-AU' : 'zh-CN';
        router.replace(`/${locale}/setup`);
      } else {
        setChecked(true);
      }
    }).catch(() => {
      // On error (e.g. 401), redirect to login
      const locale = pathname.startsWith('/en-AU') ? 'en-AU' : 'zh-CN';
      router.replace(`/${locale}/login`);
    });
  }, [pathname, router]);

  if (!checked) {
    // Blank screen while checking — prevents flash of protected content
    return null;
  }

  return <>{children}</>;
}

export default AuthGuard;
