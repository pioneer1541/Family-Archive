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
          background: '#2a2118',
          color: '#f5f0e8',
          fontSize: 196,
          fontWeight: 700
        }}
      >
        FV
      </div>
    ),
    size
  );
}
