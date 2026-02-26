import type {BilingualText, UiLocale} from '@src/lib/api/types';

export interface BilingualDisplay {
  text: string;
  fallback: boolean;
  fallbackLabel: 'EN' | '中' | '';
}

export function pickBilingualText(value: BilingualText, locale: UiLocale): BilingualDisplay {
  const zh = String(value?.zh || '').trim();
  const en = String(value?.en || '').trim();

  if (locale === 'zh-CN') {
    if (zh) return {text: zh, fallback: false, fallbackLabel: ''};
    if (en) return {text: en, fallback: true, fallbackLabel: 'EN'};
    return {text: '', fallback: false, fallbackLabel: ''};
  }

  if (en) return {text: en, fallback: false, fallbackLabel: ''};
  if (zh) return {text: zh, fallback: true, fallbackLabel: '中'};
  return {text: '', fallback: false, fallbackLabel: ''};
}
