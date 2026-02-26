'use client';

const STORAGE_KEY = 'fkv:agent-recent-questions:v1';
const MAX_STORED = 20;
const CHANGE_EVENT = 'fkv:agent-recent-questions:changed';

function canUseStorage(): boolean {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined';
}

function normalizeQuestion(input: string): string {
  return String(input || '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 160);
}

function readRaw(): string[] {
  if (!canUseStorage()) return [];
  try {
    const payload = window.localStorage.getItem(STORAGE_KEY);
    if (!payload) return [];
    const rows = JSON.parse(payload);
    if (!Array.isArray(rows)) return [];
    return rows.map((item) => normalizeQuestion(String(item || ''))).filter(Boolean);
  } catch {
    return [];
  }
}

function writeRaw(rows: string[]): void {
  if (!canUseStorage()) return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(rows.slice(0, MAX_STORED)));
  } catch {
    // Ignore storage quota and privacy mode write failures.
  }
}

function emitChange(rows: string[]): void {
  if (typeof window === 'undefined') return;
  try {
    window.dispatchEvent(new CustomEvent<string[]>(CHANGE_EVENT, {detail: rows.slice()}));
  } catch {
    // no-op
  }
}

export function readRecentAgentQuestions(): string[] {
  return readRaw().slice(0, MAX_STORED);
}

export function pushRecentAgentQuestion(
  question: string,
  opts?: {source?: 'dashboard' | 'agent' | 'quick' | 'action'}
): string[] {
  const value = normalizeQuestion(question);
  if (!value) return readRecentAgentQuestions();
  if (opts?.source === 'action') return readRecentAgentQuestions();
  const prev = readRaw();
  const lowered = value.toLowerCase();
  const deduped = prev.filter((item) => item.toLowerCase() !== lowered);
  const next = [value, ...deduped].slice(0, MAX_STORED);
  writeRaw(next);
  emitChange(next);
  return next;
}

export function subscribeRecentAgentQuestions(listener: (rows: string[]) => void): () => void {
  if (typeof window === 'undefined') {
    return () => {};
  }
  const onCustom = (event: Event) => {
    const detail = (event as CustomEvent<string[] | undefined>).detail;
    if (Array.isArray(detail)) {
      listener(detail.slice(0, MAX_STORED));
      return;
    }
    listener(readRecentAgentQuestions());
  };
  const onStorage = (event: StorageEvent) => {
    if (event.key !== STORAGE_KEY) return;
    listener(readRecentAgentQuestions());
  };
  window.addEventListener(CHANGE_EVENT, onCustom as EventListener);
  window.addEventListener('storage', onStorage);
  return () => {
    window.removeEventListener(CHANGE_EVENT, onCustom as EventListener);
    window.removeEventListener('storage', onStorage);
  };
}

