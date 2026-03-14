# Family Vault 第二轮 Review 修复状态报告

**日期:** 2026-03-14  
**检查人:** 码农  

---

## 修复项状态总览

| # | 修复项 | 优先级 | 状态 | 说明 |
|---|--------|--------|------|------|
| 1 | Provider table mobile card layout data-label | MUST | ✅ | 已验证完成 |
| 2 | Ollama URL normalization /api | MUST | ✅ | 已验证完成 |
| 3 | Model-list 状态区分 | SHOULD | ✅ | 已验证完成 |
| 4 | Provider model-fetch 错误显示 | SHOULD | ✅ | 已验证完成 |
| 5 | Settings restart_required 测试隔离 | SHOULD | ✅ | 已验证完成 |
| 6 | tsconfig.tsbuildinfo 到 .gitignore | SHOULD | ✅ | 已验证完成 |
| 7 | 小屏幕手机布局优化 | OPTIONAL | ✅ | 已验证完成 |

---

## 详细验证结果

### 1. ✅ Provider table mobile card layout data-label

**位置:**
- `frontend/app/[locale]/settings/page.tsx`

**验证结果:**
- Gmail 凭证表格 (`gmail-cred-table`): 所有 `<td>` 元素都有 `data-label` 属性
- 用户表格 (`users-table`): 所有 `<td>` 元素都有 `data-label` 属性
- LLM Provider 列表使用 card 布局 (`llm-provider-card`)，非表格形式，不需要 data-label

**相关代码:**
```tsx
{/* Gmail 表格 */}
<td data-label={tg('tableName')}>{cred.name}</td>
<td data-label={tg('tableClientId')} className="gmail-cred-mono">...</td>
<td data-label={tg('tableStatus')}>...</td>
<td data-label={tg('tableCreatedAt')}>...</td>
<td data-label={tg('tableUpdatedAt')}>...</td>
<td data-label={tg('tableActions')} className="gmail-cred-actions">...</td>

{/* 用户表格 */}
<td data-label={t('username')}>{u.username}</td>
<td data-label={t('role')}>{u.role || 'user'}</td>
<td data-label={t('createdAt')}>...</td>
<td data-label={t('actions')}>...</td>
```

---

### 2. ✅ Ollama URL normalization /api

**位置:**
- `backend/app/services/llm_provider.py` (函数: `normalize_ollama_base_url`)
- `backend/tests/test_ollama_provider_url_normalization.py`

**验证结果:**
- URL 规范化函数已正确处理 `/api` 和 `/api/` 后缀
- 测试用例已覆盖 `/api` 场景

**相关代码:**
```python
def normalize_ollama_base_url(base_url: str) -> str:
    # ...
    while path:
        lower_path = path.lower()
        if lower_path == "/v1" or lower_path == "/api":
            path = ""
            break
        if lower_path.endswith("/v1"):
            path = path[:-3].rstrip("/")
            continue
        if lower_path.endswith("/api"):
            path = path[:-4].rstrip("/")
            continue
        break
    # ...
```

**测试覆盖:**
```python
def test_normalize_ollama_base_url_strips_api_suffix():
    assert normalize_ollama_base_url("http://localhost:11434/api") == "http://localhost:11434"
    assert normalize_ollama_base_url("http://localhost:11434/api/") == "http://localhost:11434"
    assert normalize_ollama_base_url("http://localhost:11434/API") == "http://localhost:11434"
```

---

### 3. ✅ Model-list 状态区分

**位置:**
- `frontend/app/[locale]/settings/page.tsx`

**验证结果:**
- `null/undefined` 状态表示未获取或加载中
- `[]` 状态表示已获取但为空
- 代码正确区分了这两种状态

**相关代码:**
```tsx
// 状态定义
const [llmProviderModels, setLlmProviderModels] = useState<Record<string, string[] | null>>({});

// 加载中状态 (null)
{!isLocalProvider && selectedProviderModels === null && !selectedProviderModelError && (
  <p className="settings-hint">{t('loading')}</p>
)}

// 已获取但为空 ([])
{!isLocalProvider && selectedProviderModels !== null && !selectedProviderModelError && selectedProviderModels.length === 0 && (
  <p className="settings-hint">{t('modelListEmpty')}</p>
)}

// 错误状态
{selectedProviderModelError && (
  <p className="settings-hint" style={{color: '#c0392b'}}>
    {t('llmProviderModelsLoadFailedOne', {name: selectedProvider?.name || selectedProviderId})}
  </p>
)}
```

---

### 4. ✅ Provider model-fetch 错误显示

**位置:**
- `frontend/app/[locale]/settings/page.tsx`

**验证结果:**
- 使用 toast 通知显示错误
- Inline 错误显示在每个 provider card 中
- 测试连接时也显示错误

**相关代码:**
```tsx
// Toast 通知
if (failedNames.length > 0) {
  setToast(t('llmProviderModelsLoadFailed', {names: failedNames.join(', ')}));
  setTimeout(() => setToast(''), 5000);
}

// Inline 错误
{providerModelError && (
  <p className="settings-hint llm-provider-card-error">{providerModelError}</p>
)}

// 测试连接错误
async function handleLLMProviderTest(provider: LLMProvider) {
  // ...
  if (result.ok) {
    // ...
  } else {
    const message = getLLMProviderErrorMessage(result.error || t('llmProviderTestFail'));
    setLlmProviderModelErrors((prev) => ({...prev, [provider.id]: message}));
    setToast(message);
  }
}
```

---

### 5. ✅ Settings restart_required 测试隔离

**位置:**
- `backend/tests/test_settings_restart_required.py`

**验证结果:**
- 使用 `@pytest.fixture(autouse=True)` 自动隔离环境
- 使用 `monkeypatch.delenv` 清理环境变量
- 使用 `invalidate_runtime_cache` 清理缓存

**相关代码:**
```python
@pytest.fixture(autouse=True)
def _isolate_summary_model_env(monkeypatch):
    monkeypatch.delenv("FAMILY_VAULT_SUMMARY_MODEL", raising=False)
    monkeypatch.delenv("FAMILY_VAULT_OLLAMA_BASE_URL", raising=False)
    invalidate_runtime_cache("summary_model")
    invalidate_runtime_cache("ollama_base_url")
    yield
    monkeypatch.delenv("FAMILY_VAULT_SUMMARY_MODEL", raising=False)
    monkeypatch.delenv("FAMILY_VAULT_OLLAMA_BASE_URL", raising=False)
    invalidate_runtime_cache("summary_model")
    invalidate_runtime_cache("ollama_base_url")
```

---

### 6. ✅ tsconfig.tsbuildinfo 到 .gitignore

**位置:**
- `.gitignore`

**验证结果:**
- 第26行已添加 `frontend/tsconfig.tsbuildinfo`

**相关代码:**
```gitignore
# Node
frontend/node_modules/
frontend/.next/
frontend/.next-runtime/
frontend/.turbo/
frontend/test-results/
frontend/playwright-report/
frontend/tsconfig.tsbuildinfo
```

---

### 7. ✅ 小屏幕手机布局优化

**位置:**
- `frontend/app/globals.css`

**验证结果:**
- 已存在 `@media (max-width: 360px)` 媒体查询
- 已存在全面的移动端响应式设计 (`@media (max-width: 768px)`)
- LLM Provider card 在小屏幕上已优化为单列布局

**相关代码:**
```css
@media (max-width: 360px) {
  .topbar-meta {
    display: none;
  }
}

@media (max-width: 768px) {
  .llm-provider-list {
    grid-template-columns: 1fr;
  }
  .llm-provider-card {
    padding: 14px;
  }
  .llm-provider-card-header {
    flex-direction: column;
  }
  .llm-provider-card-grid {
    grid-template-columns: 1fr;
    gap: 10px;
  }
}
```

---

## 文件变更清单

所有修复项已在现有代码中实现，无需额外文件变更。

### 已验证文件:
1. ✅ `backend/app/services/llm_provider.py` - URL 规范化
2. ✅ `backend/tests/test_ollama_provider_url_normalization.py` - URL 测试
3. ✅ `backend/tests/test_settings_restart_required.py` - 测试隔离
4. ✅ `frontend/app/[locale]/settings/page.tsx` - UI 状态管理
5. ✅ `frontend/app/globals.css` - 响应式样式
6. ✅ `.gitignore` - 忽略配置

---

## 结论

所有第二轮 Review 的修复项均已实现并通过验证。代码已正确处理:

1. ✅ 移动端表格 data-label 属性
2. ✅ Ollama URL 规范化（包括 `/api`）
3. ✅ Model-list 状态区分（null vs []）
4. ✅ Provider model-fetch 错误显示（toast + inline）
5. ✅ Settings 测试环境隔离
6. ✅ tsconfig.tsbuildinfo 在 .gitignore
7. ✅ 小屏幕手机布局优化

无需额外修改，代码已符合 Review 要求。
