import {createMockAdapter} from './adapters/mock';
import {createRealAdapter} from './adapters/real';
import type {KbApiClient} from './types';

let clientSingleton: KbApiClient | null = null;

function makeClient(): KbApiClient {
  const mode = String(process.env.NEXT_PUBLIC_KB_API_MODE || 'real').trim().toLowerCase();
  if (mode === 'mock') return createMockAdapter();
  return createRealAdapter();
}

export function getKbClient(): KbApiClient {
  if (clientSingleton) return clientSingleton;
  clientSingleton = makeClient();
  return clientSingleton;
}
