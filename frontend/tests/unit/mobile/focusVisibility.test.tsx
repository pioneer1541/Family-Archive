import {describe, expect, it} from 'vitest';
import {parsePxValue, shouldScrollFocusedField} from '@src/lib/mobile/focusVisibility';

describe('parsePxValue', () => {
  it('parses px strings and falls back for invalid value', () => {
    expect(parsePxValue('57px', 10)).toBe(57);
    expect(parsePxValue(' 88 ', 10)).toBe(88);
    expect(parsePxValue('', 10)).toBe(10);
  });
});

describe('shouldScrollFocusedField', () => {
  it('returns true for middle-lower input when mobile keyboard shrinks viewport', () => {
    const shouldScroll = shouldScrollFocusedField({
      windowWidth: 390,
      viewportTop: 0,
      viewportHeight: 420,
      topbarHeight: 57,
      fixedBottomHeight: 0,
      rectTop: 388,
      rectBottom: 430
    });
    expect(shouldScroll).toBe(true);
  });

  it('returns false when focused input is already visible in safe area', () => {
    const shouldScroll = shouldScrollFocusedField({
      windowWidth: 390,
      viewportTop: 0,
      viewportHeight: 420,
      topbarHeight: 57,
      fixedBottomHeight: 0,
      rectTop: 120,
      rectBottom: 160
    });
    expect(shouldScroll).toBe(false);
  });

  it('accounts for a fixed bottom bar when deciding if the field is obscured', () => {
    const shouldScroll = shouldScrollFocusedField({
      windowWidth: 390,
      viewportTop: 0,
      viewportHeight: 700,
      topbarHeight: 57,
      fixedBottomHeight: 60,
      rectTop: 610,
      rectBottom: 650
    });
    expect(shouldScroll).toBe(true);
  });

  it('treats near-top focused input as obscured by sticky topbar mask', () => {
    const shouldScroll = shouldScrollFocusedField({
      windowWidth: 390,
      viewportTop: 0,
      viewportHeight: 640,
      topbarHeight: 57,
      fixedBottomHeight: 0,
      rectTop: 72,
      rectBottom: 110
    });
    expect(shouldScroll).toBe(true);
  });
});
