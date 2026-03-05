import {ImageResponse} from 'next/og';

export const contentType = 'image/png';
export const size = {
  width: 180,
  height: 180
};

export default function AppleIcon() {
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
          borderRadius: 36,
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 120,
            height: 120,
            background: 'rgba(255, 255, 255, 0.15)',
            borderRadius: 24,
          }}
        >
          <span style={{ fontSize: 64, fontWeight: 700, color: '#fff' }}>
            FV
          </span>
        </div>
      </div>
    ),
    size
  );
}
