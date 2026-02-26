const STORAGE_KEY = 'fkv:cat-alias:v1';

export type CategoryAliasMap = Record<string, string>;

function canUseStorage(): boolean {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined';
}

export function readCategoryAliasMap(): CategoryAliasMap {
  if (!canUseStorage()) return {};
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return {};
    return Object.fromEntries(
      Object.entries(parsed)
        .map(([key, value]) => [String(key || '').trim(), String(value || '').trim()])
        .filter(([key, value]) => key && value)
    );
  } catch {
    return {};
  }
}

export function writeCategoryAlias(path: string, alias: string): void {
  if (!canUseStorage()) return;
  const safePath = String(path || '').trim().toLowerCase();
  const safeAlias = String(alias || '').trim();
  if (!safePath || !safeAlias) return;
  const now = readCategoryAliasMap();
  now[safePath] = safeAlias;
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(now));
}

export function removeCategoryAlias(path: string): void {
  if (!canUseStorage()) return;
  const safePath = String(path || '').trim().toLowerCase();
  if (!safePath) return;
  const now = readCategoryAliasMap();
  delete now[safePath];
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(now));
}
