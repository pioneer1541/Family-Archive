'use client';

import {useToast} from '@src/lib/ui-state/toast';

export function Toast() {
  const {message, visible} = useToast();
  return (
    <div id="toast" className={`success-toast${visible ? ' show' : ''}`}>
      {message}
    </div>
  );
}
