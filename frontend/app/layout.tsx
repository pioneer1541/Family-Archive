import type {Metadata, Viewport} from 'next';
import type {ReactNode} from 'react';
import './globals.css';

export const metadata: Metadata = {
  title: {
    default: 'Family Vault',
    template: '%s | Family Vault'
  },
  applicationName: 'Family Vault',
  manifest: '/manifest.webmanifest',
  appleWebApp: {
    capable: true,
    statusBarStyle: 'default',
    title: 'Family Vault'
  },
  formatDetection: {
    telephone: false,
    date: false,
    email: false,
    address: false
  },
  themeColor: '#667eea',
  icons: {
    apple: [
      {url: '/apple-icon?size=180', sizes: '180x180', type: 'image/png'},
      {url: '/apple-icon?size=512', sizes: '512x512', type: 'image/png'}
    ],
    icon: [
      {url: '/icon?size=192', sizes: '192x192', type: 'image/png'},
      {url: '/icon?size=512', sizes: '512x512', type: 'image/png'}
    ]
  }
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover',
  themeColor: '#667eea'
};

export default function RootLayout({children}: {children: ReactNode}) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
