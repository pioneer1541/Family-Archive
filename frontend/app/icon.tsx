import {ImageResponse} from 'next/og';

export const contentType = 'image/png';
export const size = {
  width: 512,
  height: 512
};

export default function Icon() {
  return new ImageResponse(
    (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
          borderRadius: 80,
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 320,
            height: 320,
            background: 'rgba(255, 255, 255, 0.15)',
            borderRadius: 60,
          }}
        >
          <span style={{ fontSize: 180, fontWeight: 700, color: '#fff' }}>
            FV
          </span>
        </div>
      </div>
    ),
    size
  );
}
