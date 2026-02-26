export const CATEGORY_ICON_MAP: Record<string, string> = {
  home: '🏠',
  finance: '🧾',
  legal: '📋',
  health: '🏥',
  work: '💼',
  tech: '💻',
  education: '📚',
  media: '🎬',
  personal: '👤',
  archive: '📦'
};

export function topLevelCategory(path: string): string {
  const value = String(path || '').trim().toLowerCase();
  if (!value) return 'archive';
  const left = value.split('/')[0] || 'archive';
  return left;
}

export function iconForCategory(path: string): string {
  return CATEGORY_ICON_MAP[topLevelCategory(path)] || '📂';
}

export function catIdFromPath(path: string): string {
  return String(path || '')
    .trim()
    .toLowerCase()
    .replace(/\//g, '__')
    .replace(/[^a-z0-9_-]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '');
}

export function pathFromCatId(catId: string): string {
  return String(catId || '').replace(/__/g, '/');
}

export function colorIndexForCategory(path: string, total = 6): number {
  const raw = String(path || 'archive/misc');
  let h = 0;
  for (let i = 0; i < raw.length; i += 1) {
    h = (h * 31 + raw.charCodeAt(i)) >>> 0;
  }
  return Math.abs(h) % Math.max(1, total);
}
