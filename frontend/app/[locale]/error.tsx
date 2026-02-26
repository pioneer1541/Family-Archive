'use client';

export default function LocaleError({
  error,
  reset
}: {
  error: Error & {digest?: string};
  reset: () => void;
}) {
  return (
    <main style={{padding: '24px'}}>
      <h1>页面加载异常 / Page Load Error</h1>
      <p>当前页面加载失败，请重试。</p>
      <p>The page failed to load. Please retry.</p>
      <p style={{fontFamily: 'monospace', fontSize: '12px'}}>{String(error?.digest || error?.message || 'unknown_error')}</p>
      <button type="button" onClick={() => reset()}>
        重试 / Retry
      </button>
    </main>
  );
}
