'use client';

import {useEffect, useState} from 'react';
import {useRouter, usePathname} from 'next/navigation';
import {getKbClient} from '@src/lib/api/kb-client';
import {isPublicPath} from '@src/lib/utils/paths';

const AUTH_STATUS_TIMEOUT_MS = 8000;

function withTimeout<T>(promise: Promise<T>, timeoutMs: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timerId = window.setTimeout(() => {
      reject(new Error('auth status timeout'));
    }, timeoutMs);

    promise.then((value) => {
      window.clearTimeout(timerId);
      resolve(value);
    }).catch((error) => {
      window.clearTimeout(timerId);
      reject(error);
    });
  });
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

    let isMounted = true;
    const locale = pathname.startsWith('/en-AU') ? 'en-AU' : 'zh-CN';
    const safeReplace = (target: string) => {
      if (!isMounted) return;
      router.replace(target);
    };

    withTimeout(client.getAuthStatus(), AUTH_STATUS_TIMEOUT_MS).then(async (status) => {
      if (!isMounted) return;
      if (!status.setup_complete) {
        safeReplace(`/${locale}/setup`);
        return;
      }
      if (client.getMe) {
        const me = await client.getMe();
        if (!isMounted) return;
        if (!me) {
          safeReplace(`/${locale}/login`);
          return;
        }
      }
      if (!isMounted) return;
      setChecked(true);
    }).catch(() => {
      if (!isMounted) return;
      safeReplace(`/${locale}/login`);
    });

    return () => {
      isMounted = false;
    };
  }, [pathname, router]);

  if (!checked) {
    // Blank screen while checking — prevents flash of protected content
    return null;
  }

  return <>{children}</>;
}

export default AuthGuard;
