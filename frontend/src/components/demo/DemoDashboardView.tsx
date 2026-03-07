'use client';

import {useEffect, useMemo, useState} from 'react';
import {useLocale, useTranslations} from 'next-intl';
import {getKbClient} from '@src/lib/api/kb-client';
import type {KbCategory, KbDoc, UiLocale} from '@src/lib/api/types';
import {useRouter} from '@/i18n/navigation';

// Demo Dashboard - 优化版界面设计
// 设计方向：温暖极简主义 + 精致细节

function DemoDashboardView() {
  const t = useTranslations();
  const locale = useLocale() as UiLocale;
  const router = useRouter();
  const client = useMemo(() => getKbClient(), []);

  const [docs, setDocs] = useState<KbDoc[]>([]);
  const [categories, setCategories] = useState<KbCategory[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [dashQuestion, setDashQuestion] = useState('');

  useEffect(() => {
    let alive = true;
    setIsLoading(true);
    Promise.all([client.getDocs(), client.getCategories()])
      .then(([docRows, categoryRows]) => {
        if (!alive) return;
        setDocs(docRows);
        setCategories(categoryRows);
      })
      .catch(() => {
        if (!alive) return;
        setDocs([]);
        setCategories([]);
      })
      .finally(() => {
        if (alive) setIsLoading(false);
      });

    return () => { alive = false; };
  }, [client]);

  const recentDocs = docs.slice(0, 6);

  return (
    <div className="demo-dashboard">
      {/* Hero Section - 渐变背景 + 动画 */}
      <section className="demo-hero">
        <div className="demo-hero-content">
          <h1 className="demo-hero-title">
            <span className="demo-title-line">家庭知识</span>
            <span className="demo-title-line demo-title-accent">智能归档</span>
          </h1>
          <p className="demo-hero-subtitle">
            {locale === 'zh-CN' ? '智能整理，随时检索' : 'Smart archive, instant access'}
          </p>
          
          {/* 搜索框 - 居中突出 */}
          <div className="demo-search-container">
            <div className="demo-search-box">
              <svg className="demo-search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="11" cy="11" r="8"/>
                <path d="M21 21l-4.35-4.35"/>
              </svg>
              <input
                type="text"
                className="demo-search-input"
                placeholder={locale === 'zh-CN' ? '搜索文档、账单、照片...' : 'Search documents, bills, photos...'}
                value={dashQuestion}
                onChange={(e) => setDashQuestion(e.target.value)}
              />
              <button className="demo-search-btn">
                {locale === 'zh-CN' ? '搜索' : 'Search'}
              </button>
            </div>
          </div>
        </div>
        
        {/* 装饰性背景元素 */}
        <div className="demo-hero-decoration">
          <div className="demo-circle demo-circle-1"></div>
          <div className="demo-circle demo-circle-2"></div>
          <div className="demo-circle demo-circle-3"></div>
        </div>
      </section>

      {/* Stats Section - 玻璃态卡片 */}
      <section className="demo-stats">
        <div className="demo-stats-grid">
          <div className="demo-stat-card">
            <div className="demo-stat-icon">📄</div>
            <div className="demo-stat-content">
              <span className="demo-stat-value">{docs.length}</span>
              <span className="demo-stat-label">{locale === 'zh-CN' ? '文档' : 'Documents'}</span>
            </div>
          </div>
          <div className="demo-stat-card">
            <div className="demo-stat-icon">📁</div>
            <div className="demo-stat-content">
              <span className="demo-stat-value">{categories.length}</span>
              <span className="demo-stat-label">{locale === 'zh-CN' ? '分类' : 'Categories'}</span>
            </div>
          </div>
          <div className="demo-stat-card">
            <div className="demo-stat-icon">⚡</div>
            <div className="demo-stat-content">
              <span className="demo-stat-value">AI</span>
              <span className="demo-stat-label">{locale === 'zh-CN' ? '智能助手' : 'AI Ready'}</span>
            </div>
          </div>
        </div>
      </section>

      {/* Recent Documents - 卡片列表 */}
      <section className="demo-recent">
        <div className="demo-section-header">
          <h2>{locale === 'zh-CN' ? '最近文档' : 'Recent Documents'}</h2>
          <button className="demo-link-btn" onClick={() => router.push('/docs')}>
            {locale === 'zh-CN' ? '查看全部' : 'View All'} →
          </button>
        </div>
        
        {isLoading ? (
          <div className="demo-skeleton-grid">
            {[1, 2, 3, 4, 5, 6].map((i) => (
              <div key={i} className="demo-skeleton-card">
                <div className="demo-skeleton demo-skeleton-title"></div>
                <div className="demo-skeleton demo-skeleton-text"></div>
                <div className="demo-skeleton demo-skeleton-text demo-skeleton-short"></div>
              </div>
            ))}
          </div>
        ) : recentDocs.length === 0 ? (
          <div className="demo-empty-state">
            <div className="demo-empty-icon">📭</div>
            <p>{locale === 'zh-CN' ? '暂无文档，开始添加吧' : 'No documents yet'}</p>
          </div>
        ) : (
          <div className="demo-docs-grid">
            {recentDocs.map((doc, index) => (
              <article 
                key={doc.id} 
                className="demo-doc-card"
                style={{'--delay': `${index * 50}ms`} as React.CSSProperties}
              >
                <div className="demo-doc-header">
                  <span className="demo-doc-ext">{doc.fileExt || 'doc'}</span>
                  <span className="demo-doc-date">
                    {new Date(doc.updatedAt || Date.now()).toLocaleDateString(locale === 'zh-CN' ? 'zh-CN' : 'en-AU')}
                  </span>
                </div>
                <h3 className="demo-doc-title">{doc.titleZh || doc.titleEn || doc.fileName || 'Untitled'}</h3>
                <p className="demo-doc-preview">
                  {(doc.summaryZh || doc.summaryEn || '').slice(0, 80)}...
                </p>
              </article>
            ))}
          </div>
        )}
      </section>

      {/* Quick Actions - 圆形按钮组 */}
      <section className="demo-actions">
        <div className="demo-section-header">
          <h2>{locale === 'zh-CN' ? '快捷操作' : 'Quick Actions'}</h2>
        </div>
        <div className="demo-actions-grid">
          <button className="demo-action-btn" onClick={() => router.push('/docs')}>
            <span className="demo-action-icon">📤</span>
            <span className="demo-action-label">{locale === 'zh-CN' ? '上传文档' : 'Upload'}</span>
          </button>
          <button className="demo-action-btn" onClick={() => router.push('/agent')}>
            <span className="demo-action-icon">🤖</span>
            <span className="demo-action-label">{locale === 'zh-CN' ? 'AI 助手' : 'AI Chat'}</span>
          </button>
          <button className="demo-action-btn" onClick={() => router.push('/cats')}>
            <span className="demo-action-icon">🗂️</span>
            <span className="demo-action-label">{locale === 'zh-CN' ? '分类管理' : 'Categories'}</span>
          </button>
          <button className="demo-action-btn" onClick={() => router.push('/settings')}>
            <span className="demo-action-icon">⚙️</span>
            <span className="demo-action-label">{locale === 'zh-CN' ? '系统设置' : 'Settings'}</span>
          </button>
        </div>
      </section>
    </div>
  );
}

export default DemoDashboardView;
