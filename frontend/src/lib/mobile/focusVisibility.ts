export function parsePxValue(raw: string | null | undefined, fallback: number): number {
  const parsed = Number.parseInt(String(raw || '').replace('px', '').trim(), 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function isEditableField(target: EventTarget | null): target is HTMLElement {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  if (target instanceof HTMLTextAreaElement) return !target.disabled && !target.readOnly;
  if (target instanceof HTMLInputElement) {
    const blockedTypes = new Set([
      'button',
      'checkbox',
      'color',
      'file',
      'hidden',
      'image',
      'radio',
      'range',
      'reset',
      'submit'
    ]);
    return !blockedTypes.has(String(target.type || '').toLowerCase()) && !target.disabled && !target.readOnly;
  }
  return false;
}

export function shouldScrollFocusedField(params: {
  windowWidth: number;
  viewportTop: number;
  viewportHeight: number;
  topbarHeight: number;
  fixedBottomHeight: number;
  rectTop: number;
  rectBottom: number;
}): boolean {
  if (params.windowWidth > 768) return false;
  // Extra top buffer avoids sticky-header overlap/mask on mobile while keyboard is open.
  const safeTop = params.viewportTop + params.topbarHeight + 20;
  const safeBottom = params.viewportTop + params.viewportHeight - params.fixedBottomHeight - 12;
  return params.rectTop < safeTop || params.rectBottom > safeBottom;
}
