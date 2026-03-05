import type {MetadataRoute} from 'next';

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: 'Family Vault',
    short_name: 'Family Vault',
    description: 'Family archive workspace with bilingual search and AI agent.',
    start_url: '/zh-CN/dashboard',
    scope: '/',
    display: 'standalone',
    background_color: '#667eea',
    theme_color: '#667eea',
    icons: [
      {
        src: '/icon?size=192',
        sizes: '192x192',
        type: 'image/png'
      },
      {
        src: '/icon?size=512',
        sizes: '512x512',
        type: 'image/png'
      },
      {
        src: '/apple-icon?size=180',
        sizes: '180x180',
        type: 'image/png'
      }
    ]
  };
}
