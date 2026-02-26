'use client';

export default function GlobalError({
  error,
  reset
}: {
  error: Error & {digest?: string};
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body>
        <main style={{padding: '24px', fontFamily: 'sans-serif'}}>
          <h1>Something went wrong</h1>
          <p>Global error boundary triggered.</p>
          <p style={{fontFamily: 'monospace', fontSize: '12px'}}>{String(error?.digest || error?.message || 'unknown_error')}</p>
          <button type="button" onClick={() => reset()}>
            Retry
          </button>
        </main>
      </body>
    </html>
  );
}
