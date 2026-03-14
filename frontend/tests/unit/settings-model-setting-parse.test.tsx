import {describe, expect, it} from 'vitest';

import {parseModelSetting} from '@src/lib/utils/model-settings';

describe('parseModelSetting', () => {
  it('falls back to local provider when provider id is unknown', () => {
    const selection = parseModelSetting('123e4567-e89b-12d3-a456-426614174000:qwen3:8b', []);
    expect(selection).toEqual({providerId: '__local__', modelName: '123e4567-e89b-12d3-a456-426614174000:qwen3:8b'});
  });

  it('falls back to local provider for orphaned cloud provider refs', () => {
    const selection = parseModelSetting('cloud:missing-provider/gpt-4o-mini', []);
    expect(selection).toEqual({providerId: '__local__', modelName: 'gpt-4o-mini'});
  });
});
