'use client';

import type {ReactNode} from 'react';
import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {useLocale, useTranslations} from 'next-intl';
import {useRouter} from '@/i18n/navigation';
import {useSearchParams} from 'next/navigation';
import {getKbClient} from '@src/lib/api/kb-client';
import {isAgentRequestError} from '@src/lib/api/adapters/real';
import type {AgentAction, AgentCard, AgentStreamEvent, KbDoc, UiLocale} from '@src/lib/api/types';
import {pickBilingualText} from '@src/lib/i18n/bilingual';
import {readCategoryAliasMap} from '@src/lib/ui-state/category-alias';
import {pushRecentAgentQuestion, readRecentAgentQuestions, subscribeRecentAgentQuestions} from '@src/lib/ui-state/recent-agent-questions';
import {useOverlay} from '@src/lib/ui-state/overlay';
import {useTopbar} from '@src/lib/ui-state/topbar';
import {iconForCategory} from '@src/lib/category';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  relatedDocs: KbDoc[];
  actions: AgentAction[];
  card: AgentCard | null;
  actionsOpen: boolean;
  errorKind?: 'timeout' | 'network' | 'gateway' | 'server' | 'unknown';
  retryQuery?: string;
}

const FOLLOWUP_HINTS = [
  '继续',
  '刚才',
  '上一个',
  '上一轮',
  '这些',
  '这个',
  '它',
  'that one',
  'those',
  'continue',
  'previous',
  'above'
];
const FOLLOWUP_TURNS = 2;

function isFollowupQuery(query: string): boolean {
  const lowered = String(query || '').trim().toLowerCase();
  if (!lowered) return false;
  return FOLLOWUP_HINTS.some((token) => lowered.includes(token));
}

function renderInlineBold(line: string, keyPrefix: string): ReactNode[] {
  return line.split(/(\*\*[^*]+\*\*)/g).map((part, idx) => {
    const isBold = part.startsWith('**') && part.endsWith('**') && part.length > 4;
    if (isBold) {
      return <strong key={`${keyPrefix}-b-${idx}`}>{part.slice(2, -2)}</strong>;
    }
    return <span key={`${keyPrefix}-t-${idx}`}>{part}</span>;
  });
}

function renderAnswer(text: string): ReactNode[] {
  return String(text || '')
    .split('\n')
    .map((line, idx, list) => (
      <span key={`line-${idx}`}>
        {renderInlineBold(line, `l${idx}`)}
        {idx < list.length - 1 ? <br /> : null}
      </span>
    ));
}

function AgentView() {
  const t = useTranslations();
  const locale = useLocale() as UiLocale;
  const router = useRouter();
  const searchParams = useSearchParams();
  const client = useMemo(() => getKbClient(), []);
  const {openOverlay} = useOverlay();
  const {setTopbar} = useTopbar();

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [stages, setStages] = useState<Array<{stage: string; labelZh: string; labelEn: string}>>([]);
  const [aliases, setAliases] = useState<Record<string, string>>({});
  const [recentQuestions, setRecentQuestions] = useState<string[]>([]);

  const messagesRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const inputAreaRef = useRef<HTMLDivElement | null>(null);
  const autoAskHandledRef = useRef('');

  useEffect(() => {
    setAliases(readCategoryAliasMap());
  }, []);

  useEffect(() => {
    setRecentQuestions(readRecentAgentQuestions());
    return subscribeRecentAgentQuestions((rows) => {
      setRecentQuestions(rows.slice(0, 20));
    });
  }, []);

  useEffect(() => {
    setTopbar({
      title: t('nav.agent'),
      metaMode: 'text',
      count: 0,
      metaText: t('topbar.aiAssistant')
    });
  }, [setTopbar, t]);

  useEffect(() => {
    const node = messagesRef.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
  }, [messages, busy]);

  const suggestions = useMemo(() => {
    const defaults = [t('agent.suggestion1'), t('agent.suggestion2'), t('agent.suggestion3'), t('agent.suggestion4')]
      .map((item) => String(item || '').trim())
      .filter(Boolean);
    const out: string[] = [];
    for (const item of recentQuestions) {
      const value = String(item || '').trim();
      if (!value) continue;
      out.push(value);
      if (out.length >= 4) break;
    }
    for (const item of defaults) {
      if (out.length >= 4) break;
      if (out.includes(item)) continue;
      out.push(item);
    }
    return out.slice(0, 4);
  }, [recentQuestions, t]);

  const resizeInput = () => {
    const node = textareaRef.current;
    if (!node) return;
    node.style.height = 'auto';
    node.style.height = `${Math.min(node.scrollHeight, 140)}px`;
  };

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const root = document.documentElement;
    const update = () => {
      const h = Math.round(inputAreaRef.current?.offsetHeight || 88);
      root.style.setProperty('--agent-input-height', `${h}px`);
    };
    update();
    window.addEventListener('resize', update, {passive: true});
    window.visualViewport?.addEventListener('resize', update, {passive: true});
    return () => {
      window.removeEventListener('resize', update);
      window.visualViewport?.removeEventListener('resize', update);
    };
  }, [busy, input, messages.length]);

  const appendUserMessage = (text: string) => {
    setMessages((prev) => [
      ...prev,
      {
        id: `u-${Date.now()}-${prev.length}`,
        role: 'user',
        text,
        relatedDocs: [],
        actions: [],
        card: null,
        actionsOpen: false
      }
    ]);
  };

  const appendAssistantMessage = (
    text: string,
    relatedDocs: KbDoc[],
    actions: AgentAction[] = [],
    card: AgentCard | null = null,
    opts?: {errorKind?: ChatMessage['errorKind']; retryQuery?: string}
  ) => {
    setMessages((prev) => [
      ...prev,
      {
        id: `a-${Date.now()}-${prev.length}`,
        role: 'assistant',
        text,
        relatedDocs,
        actions: actions.slice(0, 4),
        card,
        actionsOpen: false,
        errorKind: opts?.errorKind,
        retryQuery: opts?.retryQuery
      }
    ]);
  };

  const toggleActions = (messageId: string) => {
    setMessages((prev) =>
      prev.map((item) => (item.id === messageId ? {...item, actionsOpen: !item.actionsOpen} : item))
    );
  };

  const actionPrompt = (action: AgentAction): string => {
    const command = String(action.payload?.command || action.key || '').trim();
    const target = String(action.payload?.target || '').trim();
    if (action.actionType === 'navigate') {
      if (target === 'docs') {
        router.push('/docs');
        return '';
      }
      if (target === 'cats') {
        router.push('/cats');
        return '';
      }
    }
    if (command === 'queue_view') {
      return locale === 'zh-CN' ? '查看当前处理队列状态' : 'Show current processing queue status';
    }
    if (command === 'list_recent') {
      return locale === 'zh-CN' ? '最近有哪些账单需要关注？' : 'Which recent bills need attention?';
    }
    if (command === 'fallback_search') {
      return locale === 'zh-CN' ? '请执行语义检索并给我相关文档' : 'Run semantic search and return related documents';
    }
    if (command === 'compare_docs') {
      return locale === 'zh-CN' ? '请比较这些相关文档的主要差异' : 'Compare the key differences among related documents';
    }
    if (command === 'extract_fields') {
      return locale === 'zh-CN' ? '请提取这批文档的关键字段' : 'Extract key fields from these documents';
    }
    if (command === 'extract_details') {
      return locale === 'zh-CN'
        ? '请列出这些文档的详细明细，按字段逐条展示并标注缺失项'
        : 'List detailed fields for these documents, field by field, and include missing fields';
    }
    if (command === 'timeline_build') {
      return locale === 'zh-CN' ? '请按时间线整理相关事件' : 'Build a timeline from related events';
    }
    if (command === 'reprocess_doc') {
      return locale === 'zh-CN' ? '请重处理当前相关文档' : 'Reprocess the currently related documents';
    }
    if (command === 'tag_update') {
      return locale === 'zh-CN' ? '请给当前相关文档更新标签建议并执行更新' : 'Update tags for the currently related documents';
    }
    return '';
  };

  const runAction = async (message: ChatMessage, action: AgentAction) => {
    const confirmText =
      locale === 'zh-CN'
        ? action.confirmTextZh || t('agent.defaultActionConfirm')
        : action.confirmTextEn || t('agent.defaultActionConfirm');
    if (action.requiresConfirm && typeof window !== 'undefined' && !window.confirm(confirmText)) {
      return;
    }
    const prompt = actionPrompt(action);
    if (!prompt) return;
    const selectedDocIds = message.relatedDocs.map((doc) => String(doc.id || '').trim()).filter(Boolean).slice(0, 12);
    await sendMessage(prompt, selectedDocIds.length ? {selected_doc_ids: selectedDocIds} : undefined, {recordRecent: false});
  };

  const agentErrorMessage = useCallback(
    (kind: ChatMessage['errorKind'], query: string): string => {
      if (kind === 'timeout') return t('agent.errorTimeout', {query});
      if (kind === 'network') return t('agent.errorNetwork', {query});
      if (kind === 'gateway') return t('agent.errorGateway', {query});
      if (kind === 'server') return t('agent.errorServer', {query});
      return t('agent.fallbackAnswer', {query});
    },
    [t]
  );

  const sendMessage = useCallback(async (
    sourceText?: string,
    clientContextOverrides?: Record<string, unknown>,
    opts?: {recordRecent?: boolean}
  ) => {
    if (busy) return;
    const text = String(sourceText ?? input).trim();
    if (!text) return;

    if (opts?.recordRecent !== false) {
      pushRecentAgentQuestion(text, {source: 'agent'});
    }
    appendUserMessage(text);
    setInput('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }

    setBusy(true);
    setStages([]);
    try {
      const followup = isFollowupQuery(text);
      const maxRows = Math.max(2, FOLLOWUP_TURNS * 2);
      const conversation = followup
        ? messages
            .slice(-maxRows)
            .map((item) => ({
              role: item.role,
              content: String(item.text || '')
            }))
            .filter((item) => (item.role === 'user' || item.role === 'assistant') && item.content.trim().length > 0)
        : [];
      const agentPayload = {
        query: text,
        locale,
        conversation,
        clientContext: {
          context_policy: followup ? 'followup_turn' : 'fresh_turn',
          ...(clientContextOverrides && typeof clientContextOverrides === 'object' ? clientContextOverrides : {})
        }
      };

      let resultReceived = false;
      if (client.streamAgent) {
        try {
          await client.streamAgent(agentPayload, (event: AgentStreamEvent) => {
            if (event.error) return;
            if (event.result) {
              resultReceived = true;
              appendAssistantMessage(String(event.result.answer || '').trim(), event.result.relatedDocs || [], event.result.card?.actions || [], event.result.card || null);
            } else {
              setStages((prev) => [...prev, {stage: event.stage, labelZh: event.label.zh, labelEn: event.label.en}]);
            }
          });
        } catch {
          setStages([]); // SSE failed — silently fall back to runAgent
        }
      }
      if (!resultReceived) {
        const out = await client.runAgent(agentPayload);
        appendAssistantMessage(String(out.answer || '').trim(), out.relatedDocs || [], out.card?.actions || [], out.card || null);
      }
    } catch (error) {
      const kind = isAgentRequestError(error) ? error.kind : 'unknown';
      appendAssistantMessage(agentErrorMessage(kind, text), [], [], null, {errorKind: kind, retryQuery: text});
    } finally {
      setBusy(false);
      setStages([]);
    }
  }, [agentErrorMessage, busy, client, input, locale, messages]);

  const detailSectionLabel = (sectionName: string): string => {
    const key = String(sectionName || '').trim().toLowerCase();
    if (key.includes('insurance')) return locale === 'zh-CN' ? '保险明细' : 'Insurance Details';
    if (key.includes('bill')) return locale === 'zh-CN' ? '账单明细' : 'Bill Details';
    if (key.includes('warranty')) return locale === 'zh-CN' ? '保修明细' : 'Warranty Details';
    if (key.includes('contract')) return locale === 'zh-CN' ? '合同明细' : 'Contract Details';
    return locale === 'zh-CN' ? '结构化明细' : 'Structured Details';
  };

  const detailLabel = (row: AgentCard['detailSections'][number]['rows'][number]): string => {
    if (locale === 'zh-CN') return String(row.labelZh || row.labelEn || row.field || '');
    return String(row.labelEn || row.labelZh || row.field || '');
  };

  const detailValue = (row: AgentCard['detailSections'][number]['rows'][number]): string => {
    if (locale === 'zh-CN') return String(row.valueZh || row.valueEn || '');
    return String(row.valueEn || row.valueZh || '');
  };

  useEffect(() => {
    const ask = String(searchParams?.get('ask') || '').trim();
    const autostart = String(searchParams?.get('autostart') || '').trim() === '1';
    const src = String(searchParams?.get('src') || '').trim();
    if (!ask || !autostart) {
      autoAskHandledRef.current = '';
      return;
    }
    const key = `${ask}::${autostart ? '1' : '0'}`;
    if (autoAskHandledRef.current === key) return;
    autoAskHandledRef.current = key;
    void sendMessage(ask, undefined, {recordRecent: src !== 'dashboard'}).finally(() => {
      router.replace('/agent');
    });
  }, [router, searchParams, sendMessage]);

  const categoryLabel = (doc: KbDoc): string => {
    const alias = String(aliases[doc.categoryPath] || '').trim();
    if (alias) return alias;
    const selected = pickBilingualText(doc.categoryLabel, locale).text;
    return selected || doc.categoryPath;
  };

  return (
    <div className="view active" id="view-agent">
      <div className="agent-layout">
        <div className="agent-messages" id="agent-messages" ref={messagesRef}>
          {messages.length === 0 ? (
            <div className="agent-welcome">
              <div className="agent-welcome-icon">✦</div>
              <div className="agent-welcome-title">{t('agent.welcomeTitle')}</div>
              <div className="agent-welcome-sub">{t('agent.welcomeSub')}</div>
              <div className="agent-suggestions">
                {suggestions.map((item) => (
                  <button
                    key={item}
                    type="button"
                    className="agent-suggestion"
                    onClick={() => {
                      void sendMessage(item);
                    }}
                  >
                    {item}
                  </button>
                ))}
              </div>
            </div>
          ) : null}

          {messages.map((message) => (
            <div key={message.id} className={`msg ${message.role}`}>
              <div className="msg-avatar">{message.role === 'user' ? t('agent.me') : '✦'}</div>
              <div className="msg-body">
                <div className="msg-bubble">{renderAnswer(message.text)}</div>
                {message.role === 'assistant' && message.retryQuery ? (
                  <div className="msg-retry-row">
                    <button
                      type="button"
                      className="msg-actions-toggle"
                      disabled={busy}
                      onClick={() => {
                        void sendMessage(message.retryQuery, undefined, {recordRecent: false});
                      }}
                    >
                      {t('agent.retry')}
                    </button>
                  </div>
                ) : null}

                {message.role === 'assistant' && (message.card?.detailSections || []).length > 0 ? (
                  <div className="agent-detail-card">
                    {(message.card?.detailSections || []).map((section, sectionIdx) => (
                      <div key={`${message.id}-detail-${sectionIdx}`} className="agent-detail-section">
                        <div className="agent-detail-title">{detailSectionLabel(section.sectionName)}</div>
                        <div className="agent-detail-rows">
                          {section.rows.map((row, rowIdx) => {
                            const value = detailValue(row);
                            if (!value) return null;
                            return (
                              <div key={`${message.id}-row-${sectionIdx}-${rowIdx}`} className="agent-detail-row">
                                <div className="agent-detail-key">{detailLabel(row)}</div>
                                <div className="agent-detail-value">{value}</div>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    ))}
                    {(message.card?.missingFields || []).length > 0 ? (
                      <div className="agent-detail-missing">
                        <span className="agent-detail-missing-label">
                          {locale === 'zh-CN' ? '缺失字段：' : 'Missing fields: '}
                        </span>
                        <span>{(message.card?.missingFields || []).join(', ')}</span>
                      </div>
                    ) : null}
                    {message.card?.coverageStats ? (
                      <div className="agent-detail-coverage">
                        {locale === 'zh-CN'
                          ? `扫描文档 ${message.card.coverageStats.docsScanned}，命中 ${message.card.coverageStats.docsMatched}，字段填充 ${message.card.coverageStats.fieldsFilled}`
                          : `Scanned ${message.card.coverageStats.docsScanned}, matched ${message.card.coverageStats.docsMatched}, filled fields ${message.card.coverageStats.fieldsFilled}`}
                      </div>
                    ) : null}
                  </div>
                ) : null}

                {message.role === 'assistant' && message.relatedDocs.length > 0 ? (
                  <div>
                    <div className="msg-docs-label">{t('agent.relatedDocs')}</div>
                    <div className="msg-docs">
                      {message.relatedDocs.map((doc) => {
                        const title = pickBilingualText(doc.title, locale);
                        return (
                          <button key={`${message.id}-${doc.id}`} className="msg-doc-card" type="button" onClick={() => openOverlay(doc.id)}>
                            <span className="msg-doc-icon">{iconForCategory(doc.categoryPath)}</span>
                            <div className="msg-doc-info">
                              <div className="msg-doc-name">{title.text || doc.fileName}</div>
                              <div className="msg-doc-meta">{categoryLabel(doc)}</div>
                            </div>
                            <span className="msg-doc-arrow">›</span>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                ) : null}

                {message.role === 'assistant' && message.actions.length > 0 ? (
                  <div>
                    <button
                      type="button"
                      className="msg-actions-toggle"
                      onClick={() => {
                        toggleActions(message.id);
                      }}
                    >
                      {message.actionsOpen ? t('agent.hideActions') : t('agent.moreActions')}
                    </button>
                    {message.actionsOpen ? (
                      <div className="msg-actions-list">
                        {message.actions.map((action) => (
                          <button
                            key={`${message.id}-${action.key}`}
                            type="button"
                            className="msg-action-btn"
                            onClick={() => {
                              void runAction(message, action);
                            }}
                          >
                            {locale === 'zh-CN' ? action.labelZh || action.labelEn : action.labelEn || action.labelZh}
                          </button>
                        ))}
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </div>
            </div>
          ))}

          {busy ? (
            <div className="msg assistant">
              <div className="msg-avatar">✦</div>
              <div className="msg-body">
                {stages.length > 0 ? (
                  <div className="agent-progress">
                    {stages.map((s, idx) => (
                      <div key={idx} className="agent-progress-stage agent-progress-done">
                        <span className="agent-progress-check">✓</span>
                        <span className="agent-progress-label">{locale === 'zh-CN' ? s.labelZh : s.labelEn}</span>
                      </div>
                    ))}
                    <div className="agent-progress-stage agent-progress-current">
                      <span className="typing-bubble-inline">
                        <span className="typing-dot" />
                        <span className="typing-dot" />
                        <span className="typing-dot" />
                      </span>
                    </div>
                  </div>
                ) : (
                  <div className="typing-bubble">
                    <div className="typing-dot" />
                    <div className="typing-dot" />
                    <div className="typing-dot" />
                  </div>
                )}
              </div>
            </div>
          ) : null}
        </div>

        <div className="agent-input-area" ref={inputAreaRef}>
          <div className="agent-input-row">
            <textarea
              className="agent-textarea"
              id="agent-input"
              ref={textareaRef}
              rows={1}
              value={input}
              placeholder={t('agent.inputPlaceholder')}
              onChange={(event) => {
                setInput(event.target.value);
                resizeInput();
              }}
              onKeyDown={(event) => {
                if (event.key !== 'Enter' || event.shiftKey) return;
                event.preventDefault();
                void sendMessage();
              }}
            />
            <button
              className="agent-send-btn"
              id="agent-send-btn"
              type="button"
              disabled={busy || !String(input || '').trim()}
              onClick={() => {
                void sendMessage();
              }}
            >
              <span>↑</span>
            </button>
          </div>
          <div className="agent-input-hint">{t('agent.inputHint')}</div>
        </div>
      </div>
    </div>
  );
}

export {AgentView};
export default AgentView;
