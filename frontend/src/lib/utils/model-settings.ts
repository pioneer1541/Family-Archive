import type {LLMProvider} from '@src/lib/api/types';

export const LOCAL_PROVIDER_ID = '__local__';

export function parseModelSetting(raw: string, providers: LLMProvider[]): {providerId: string; modelName: string} {
  const value = String(raw || '').trim();
  if (!value) return {providerId: LOCAL_PROVIDER_ID, modelName: ''};
  if (value.startsWith('local:')) {
    return {providerId: LOCAL_PROVIDER_ID, modelName: value.slice('local:'.length).trim()};
  }
  if (value.startsWith('cloud:')) {
    const rest = value.slice('cloud:'.length).trim();
    if (rest.includes('/')) {
      const [providerRef, modelNameRaw] = rest.split('/', 2);
      const target = providers.find((item) => item.id === providerRef || item.name === providerRef);
      if (target) return {providerId: target.id, modelName: String(modelNameRaw || '').trim()};
      return {providerId: LOCAL_PROVIDER_ID, modelName: String(modelNameRaw || '').trim()};
    }
    return {providerId: LOCAL_PROVIDER_ID, modelName: value};
  }
  const idx = value.indexOf(':');
  if (idx > 0 && idx < value.length - 1) {
    const left = value.slice(0, idx).trim();
    const right = value.slice(idx + 1).trim();
    const target = providers.find((item) => item.id === left);
    if (target) return {providerId: target.id, modelName: right};
    return {providerId: LOCAL_PROVIDER_ID, modelName: value};
  }
  return {providerId: LOCAL_PROVIDER_ID, modelName: value};
}

export function encodeModelSetting(selection: {providerId: string; modelName: string}): string {
  const modelName = String(selection.modelName || '').trim();
  if (!modelName) return '';
  if (selection.providerId === LOCAL_PROVIDER_ID) return modelName;
  return `${selection.providerId}:${modelName}`;
}
