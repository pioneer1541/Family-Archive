const LOCALE_PREFIX_RE = /^\/(zh-CN|en-AU)(?=\/|$)/;
const DEFAULT_PUBLIC_PATHS = ['/setup', '/login', '/register'] as const;

export function normalizePath(pathname: string): string {
  return String(pathname || '').replace(LOCALE_PREFIX_RE, '') || '/';
}

export function isPublicPath(pathname: string, publicPaths: readonly string[] = DEFAULT_PUBLIC_PATHS): boolean {
  const normalized = normalizePath(pathname);
  return publicPaths.some((path) => normalized === path || normalized.startsWith(path + '/'));
}

