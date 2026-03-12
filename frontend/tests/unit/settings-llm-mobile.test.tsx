import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import SettingsPage from '../../app/[locale]/settings/page';

type ProviderRow = {
  id: string;
  name: string;
  provider_type: 'openai' | 'ollama' | 'kimi' | 'glm' | 'custom';
  base_url: string;
  has_api_key?: boolean;
  model_name: string;
  is_active: boolean;
  is_default: boolean;
  created_at: string;
  updated_at: string;
};

const nowIso = '2026-03-11T00:00:00Z';
let providersState: ProviderRow[] = [];

const getSettingsMock = vi.fn();
const getOllamaModelsMock = vi.fn();
const getLLMProvidersMock = vi.fn();
const getLLMProviderModelsMock = vi.fn();
const createLLMProviderMock = vi.fn();
const updateLLMProviderMock = vi.fn();
const validateLLMProviderMock = vi.fn();

vi.mock('next-intl', () => ({
  useLocale: () => 'zh-CN',
  useTranslations: () => (key: string) => key,
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({replace: vi.fn(), push: vi.fn(), back: vi.fn()}),
  usePathname: () => '/zh-CN/settings',
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('@src/lib/api/kb-client', () => ({
  getKbClient: () => ({
    getSettings: getSettingsMock,
    getOllamaModels: getOllamaModelsMock,
    getLLMProviders: getLLMProvidersMock,
    getLLMProviderModels: getLLMProviderModelsMock,
    createLLMProvider: createLLMProviderMock,
    updateLLMProvider: updateLLMProviderMock,
    validateLLMProvider: validateLLMProviderMock,
    getKeywords: vi.fn().mockResolvedValue({person_keywords: {}, pet_keywords: {}, location_keywords: {}}),
    getMe: vi.fn().mockResolvedValue({id: 'u-1', username: 'admin', role: 'admin', created_at: nowIso, email: null}),
    getGmailCredentials: vi.fn().mockResolvedValue([]),
    listUsers: vi.fn().mockResolvedValue({total: 0, items: []}),
  }),
}));

describe('Settings LLM provider/mobile behavior', () => {
  beforeEach(() => {
    providersState = [
      {
        id: 'cloud-1',
        name: 'Cloud One',
        provider_type: 'openai',
        base_url: 'https://api.cloud-one.example/v1',
        has_api_key: true,
        model_name: 'cloud-b',
        is_active: true,
        is_default: true,
        created_at: nowIso,
        updated_at: nowIso,
      },
    ];
    getSettingsMock.mockReset();
    getSettingsMock.mockResolvedValue([
      {key: 'summary_model', value: 'cloud-1:cloud-b', label_zh: 'summary_model', label_en: 'summary_model'},
    ]);
    getOllamaModelsMock.mockReset();
    getOllamaModelsMock.mockResolvedValue([{name: 'local-x', size: 1}, {name: 'local-y', size: 1}]);
    getLLMProvidersMock.mockReset();
    getLLMProvidersMock.mockImplementation(async () => providersState);
    getLLMProviderModelsMock.mockReset();
    getLLMProviderModelsMock.mockImplementation(async (id: string) => {
      if (id === 'cloud-1') return ['cloud-a', 'cloud-b'];
      if (id === 'cloud-2') return ['cloud-c', 'cloud-d'];
      return [];
    });
    createLLMProviderMock.mockReset();
    createLLMProviderMock.mockImplementation(async (payload: Record<string, unknown>) => {
      const next: ProviderRow = {
        id: 'cloud-2',
        name: String(payload.name || 'Cloud Two'),
        provider_type: 'openai',
        base_url: String(payload.base_url || 'https://api.cloud-two.example/v1'),
        model_name: String(payload.model_name || ''),
        is_active: Boolean(payload.is_active ?? true),
        is_default: false,
        created_at: nowIso,
        updated_at: nowIso,
      };
      providersState = [...providersState, next];
      return next;
    });
    updateLLMProviderMock.mockReset();
    updateLLMProviderMock.mockImplementation(async (id: string, payload: Record<string, unknown>) => {
      const target = providersState.find((item) => item.id === id) ?? providersState[0];
      return {...target, ...payload, id} as ProviderRow;
    });
    validateLLMProviderMock.mockReset();
    validateLLMProviderMock.mockImplementation(async (payload: Record<string, unknown>) => ({
      ok: true,
      latency_ms: 18,
      models: payload.base_url === 'https://api.cloud-two.example/v1' ? ['cloud-c', 'cloud-d'] : ['cloud-a', 'cloud-b'],
      normalized_base_url: String(payload.base_url || ''),
      error: null,
    }));
  });

  it('fetches models for newly created cloud provider', async () => {
    render(<SettingsPage />);

    await waitFor(() => {
      expect(getLLMProviderModelsMock).toHaveBeenCalledWith('cloud-1');
    });

    fireEvent.click(screen.getByRole('button', {name: 'llmProviderAdd'}));
    const form = document.querySelector('.settings-form') as HTMLFormElement | null;
    expect(form).toBeTruthy();
    if (!form) return;

    const nameInput = form.querySelector('input[type="text"]') as HTMLInputElement;
    const baseUrlInput = form.querySelector('input[type="url"]') as HTMLInputElement;
    const modelNameInput = form.querySelectorAll('input[type="text"]')[1] as HTMLInputElement;

    fireEvent.change(nameInput, {target: {value: 'Cloud Two'}});
    fireEvent.change(baseUrlInput, {target: {value: 'https://api.cloud-two.example/v1'}});
    fireEvent.change(modelNameInput, {target: {value: 'manual-model'}});
    fireEvent.submit(form);

    await waitFor(() => {
      expect(validateLLMProviderMock).toHaveBeenCalledTimes(1);
      expect(createLLMProviderMock).toHaveBeenCalledTimes(1);
    });
    await waitFor(() => {
      expect(getLLMProviderModelsMock).toHaveBeenCalledWith('cloud-2');
    });
  });

  it('shows only selected cloud provider models in model selector', async () => {
    render(<SettingsPage />);

    await waitFor(() => {
      expect(getLLMProviderModelsMock).toHaveBeenCalledWith('cloud-1');
    });

    const modelFieldLabel = await screen.findByText('summary_model');
    const modelField = modelFieldLabel.closest('.settings-field');
    expect(modelField).toBeTruthy();
    if (!modelField) return;

    const selects = modelField.querySelectorAll('select');
    expect(selects.length).toBeGreaterThanOrEqual(2);
    const providerSelect = selects[0] as HTMLSelectElement;
    const modelSelect = selects[1] as HTMLSelectElement;

    expect(providerSelect.value).toBe('cloud-1');
    const modelOptions = Array.from(modelSelect.options).map((opt) => opt.value);

    expect(modelOptions).toContain('cloud-a');
    expect(modelOptions).toContain('cloud-b');
    expect(modelOptions).not.toContain('local-x');
    expect(modelOptions).not.toContain('local-y');

    expect(document.querySelectorAll('.llm-provider-card').length).toBe(1);
    expect(document.querySelector('.llm-provider-card-grid')).toBeTruthy();
  });

  it('shows empty model state separately from fetch failure', async () => {
    getOllamaModelsMock.mockResolvedValueOnce([]);
    render(<SettingsPage />);
    await waitFor(() => {
      expect(screen.getAllByText('modelListEmpty').length).toBeGreaterThan(0);
    });
    expect(screen.queryByText('modelLoadError')).not.toBeInTheDocument();
  });

  it('surfaces provider model fetch failure instead of silently treating it as empty', async () => {
    getLLMProviderModelsMock.mockRejectedValueOnce(new Error('timeout'));
    render(<SettingsPage />);

    await waitFor(() => {
      expect(screen.getByText('llmProviderModelsLoadFailed')).toBeInTheDocument();
    });
  });

  it('keeps form open and blocks create when validation fails', async () => {
    validateLLMProviderMock.mockRejectedValueOnce(new Error('llm_provider_api_key_required'));
    render(<SettingsPage />);

    const addButton = await screen.findByRole('button', {name: 'llmProviderAdd'});
    fireEvent.click(addButton);
    const form = document.querySelector('.settings-form') as HTMLFormElement;
    const textInputs = form.querySelectorAll('input[type="text"]');
    const baseUrlInput = form.querySelector('input[type="url"]') as HTMLInputElement;

    fireEvent.change(textInputs[0], {target: {value: 'Cloud Two'}});
    fireEvent.change(baseUrlInput, {target: {value: 'https://api.cloud-two.example/v1'}});
    fireEvent.submit(form);

    await waitFor(() => {
      expect(validateLLMProviderMock).toHaveBeenCalledTimes(1);
    });
    expect(createLLMProviderMock).not.toHaveBeenCalled();
    expect(screen.getByText('llmProviderApiKeyRequired')).toBeInTheDocument();
    expect(document.querySelector('.settings-form')).toBeTruthy();
  });
});
