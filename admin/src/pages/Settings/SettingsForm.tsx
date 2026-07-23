import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router-dom';
import { Card, Form, Input, InputNumber, Select, Button, Space, Badge, Tag, message, Descriptions, Row, Col } from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';
import { adminFetchJson, adminFetch, showSuccess, showError } from '../../api/request';
import type { ConfigGroup } from '../../types';

const LANGUAGES = [
  { k: 'zh', l: '中文' },
  { k: 'en', l: 'English' },
  { k: 'ja', l: '日本語' },
  { k: 'ko', l: '한국어' },
  { k: 'pt', l: 'Português' },
  { k: 'es', l: 'Español' },
  { k: 'id', l: 'Bahasa Indonesia' },
];

export default function SettingsForm() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { id } = useParams<{ id: string }>();
  const [group, setGroup] = useState<ConfigGroup | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const loadGroup = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    try {
      const res = await adminFetchJson<ConfigGroup>(`/api/admin/config-groups/${id}`);
      if (res) setGroup(res);
    } catch {
      showError(t('toast.loadFailed') as string);
    } finally {
      setLoading(false);
    }
  }, [id, t]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadGroup();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadGroup]);

  async function saveConfigJson(configJson: Record<string, unknown>) {
    if (!group) return;
    setSaving(true);
    try {
      const res = await adminFetch(`/api/admin/config-groups/${group.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config_json: configJson }),
      });
      if (res && res.ok) {
        showSuccess(t('toast.saved') as string);
        setGroup((prev) => (prev ? { ...prev, config_json: configJson } : null));
      } else {
        showError(t('settings.saveFailed') as string);
      }
    } catch {
      showError(t('settings.saveFailed') as string);
    } finally {
      setSaving(false);
    }
  }

  async function testConnection(provider?: string) {
    if (!group) return;
    message.loading({ content: t('toast.testing'), key: 'test' });
    const cfg = (group.config_json || {}) as Record<string, unknown>;
    const resolvedProvider = provider || (cfg.model_provider as string) || 'anthropic';
    try {
      let res: { ok: boolean; response?: string; error?: string } | null;
      if (group.config_type === 'model_service') {
        const body: Record<string, unknown> = { provider: resolvedProvider };
        for (const key of ['anthropic_key', 'deepseek_key', 'openai_key'] as const) {
          const v = cfg[key];
          if (typeof v === 'string' && v.trim()) body[key] = v.trim();
        }
        res = await adminFetchJson(`/api/admin/config-groups/${group.id}/test`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
      } else {
        res = await adminFetchJson('/api/admin/config/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ provider: resolvedProvider }),
        });
      }
      if (res?.ok) {
        message.success({ content: `${t('toast.testSuccess')}: ${res.response}`, key: 'test' });
      } else {
        message.error({ content: `${t('toast.testFailed')}: ${res?.error || ''}`, key: 'test' });
      }
    } catch {
      message.error({ content: t('toast.testError'), key: 'test' });
    }
  }

  function renderModelServiceForm() {
    const cfg = (group?.config_json || {}) as Record<string, unknown>;
    const provider = (cfg.model_provider as string) || 'anthropic';
    const setField = (field: string, value: unknown) => {
      const next = { ...cfg, [field]: value };
      setGroup((prev) => (prev ? { ...prev, config_json: next } : null));
    };

    return (
      <Form layout="vertical">
        <Descriptions bordered column={2} size="small" style={{ marginBottom: 24 }}>
          <Descriptions.Item label={t('settings.modelProvider')}>
            {provider}
          </Descriptions.Item>
          <Descriptions.Item label="Anthropic Key">{cfg.anthropic_ready ? t('settings.configured') : t('settings.notConfigured')}</Descriptions.Item>
          <Descriptions.Item label="DeepSeek Key">{cfg.deepseek_ready ? t('settings.configured') : t('settings.notConfigured')}</Descriptions.Item>
          <Descriptions.Item label="OpenAI Key">{cfg.openai_ready ? t('settings.configured') : t('settings.notConfigured')}</Descriptions.Item>
          <Descriptions.Item label={t('settings.adminPassword')}>
            {cfg.admin_password_set ? t('settings.configured') : t('settings.notConfigured')}
          </Descriptions.Item>
        </Descriptions>

        <Form.Item label={t('settings.modelProviderLabel')}>
          <Select value={provider} onChange={(v) => setField('model_provider', v)}>
            <Select.Option value="anthropic">Anthropic (Claude)</Select.Option>
            <Select.Option value="deepseek">DeepSeek</Select.Option>
            <Select.Option value="openai">OpenAI (GPT)</Select.Option>
          </Select>
        </Form.Item>
        <Form.Item label="Anthropic API Key">
          <Input.Password
            placeholder="Leave empty to keep current"
            onChange={(e) => setField('anthropic_key', e.target.value)}
          />
        </Form.Item>
        <Form.Item label="DeepSeek API Key">
          <Input.Password
            placeholder="Leave empty to keep current"
            onChange={(e) => setField('deepseek_key', e.target.value)}
          />
        </Form.Item>
        <Form.Item label="OpenAI API Key">
          <Input.Password
            placeholder="Leave empty to keep current"
            onChange={(e) => setField('openai_key', e.target.value)}
          />
        </Form.Item>
        <Form.Item label={t('settings.adminPassword')}>
          <Input.Password
            placeholder="Leave empty to keep current"
            onChange={(e) => setField('admin_password', e.target.value)}
          />
        </Form.Item>
        <Space>
          <Button type="primary" onClick={() => saveConfigJson(cfg)} loading={saving}>
            {t('settings.saveConfig')}
          </Button>
          <Button onClick={() => testConnection(provider)}>{t('settings.testConnection')}</Button>
        </Space>
      </Form>
    );
  }

  function renderAgentForm() {
    const cfg = (group?.config_json || {}) as Record<string, unknown>;
    const setField = (field: string, value: unknown) => {
      const next = { ...cfg, [field]: value };
      setGroup((prev) => (prev ? { ...prev, config_json: next } : null));
    };

    return (
      <Form layout="vertical">
        <Descriptions bordered column={2} size="small" style={{ marginBottom: 24 }}>
          <Descriptions.Item label="Model Provider">{(cfg.model_provider as string) || t('settings.followSystem')}</Descriptions.Item>
          <Descriptions.Item label={t('settings.temperature')}>{(cfg.temperature as number) ?? 0.93}</Descriptions.Item>
          <Descriptions.Item label={t('settings.maxTokens')}>{(cfg.max_tokens as number) ?? 2048}</Descriptions.Item>
        </Descriptions>

        <Form.Item label="Model Provider (Override)">
          <Select
            value={(cfg.model_provider as string) || ''}
            onChange={(v) => setField('model_provider', v)}
            allowClear
            placeholder={t('settings.followSystem') as string}
          >
            <Select.Option value="anthropic">Anthropic (Claude)</Select.Option>
            <Select.Option value="deepseek">DeepSeek</Select.Option>
            <Select.Option value="openai">OpenAI (GPT)</Select.Option>
          </Select>
        </Form.Item>
        <Form.Item label={t('settings.temperature')}>
          <InputNumber
            min={0}
            max={2}
            step={0.01}
            value={(cfg.temperature as number) ?? 0.93}
            onChange={(v) => setField('temperature', v)}
            style={{ width: '100%' }}
          />
        </Form.Item>
        <Form.Item label={t('settings.maxTokens')}>
          <InputNumber
            min={100}
            max={4096}
            step={1}
            value={(cfg.max_tokens as number) ?? 2048}
            onChange={(v) => setField('max_tokens', v)}
            style={{ width: '100%' }}
          />
        </Form.Item>
        {LANGUAGES.map((lang) => (
          <Form.Item key={lang.k} label={`${t('settings.systemPrompt')} - ${lang.l}`}>
            <Input.TextArea
              rows={4}
              value={(cfg[`system_prompt_${lang.k}`] as string) || ''}
              onChange={(e) => setField(`system_prompt_${lang.k}`, e.target.value)}
              placeholder={t('settings.emptyUseDefault') as string}
            />
          </Form.Item>
        ))}
        <Button type="primary" onClick={() => saveConfigJson(cfg)} loading={saving}>
          {t('settings.agentSave')}
        </Button>
      </Form>
    );
  }

  function renderImageGenerationForm() {
    const cfg = (group?.config_json || {}) as Record<string, unknown>;
    const setField = (field: string, value: unknown) => {
      const next = { ...cfg, [field]: value };
      setGroup((prev) => (prev ? { ...prev, config_json: next } : null));
    };
    const provider = (cfg.provider as string) || 'volcano';

    return (
      <Form layout="vertical">

        <Descriptions bordered column={2} size="small" style={{ marginBottom: 24 }}>
          <Descriptions.Item label="Provider">{provider || '--'}</Descriptions.Item>
          <Descriptions.Item label="Model">{(cfg.model as string) || '--'}</Descriptions.Item>
          <Descriptions.Item label="Base URL">{(cfg.base_url as string) || '--'}</Descriptions.Item>
          <Descriptions.Item label="Size">{(cfg.size as string) || '--'}</Descriptions.Item>
          <Descriptions.Item label="API Key">{(cfg.api_key as string) ? t('settings.configured') : t('settings.notConfigured')}</Descriptions.Item>
          <Descriptions.Item label="Access Key ID">{(cfg.access_key_id as string) ? t('settings.configured') : t('settings.notConfigured')}</Descriptions.Item>
          <Descriptions.Item label="Session Token">{(cfg.session_token as string) ? t('settings.configured') : t('settings.notConfigured')}</Descriptions.Item>
        </Descriptions>

        <Form.Item label="Provider">
          <Select
            value={provider}
            onChange={(v) => setField('provider', v)}
            placeholder="仅支持火山引擎"
          >
            <Select.Option value="volcano">火山引擎 (Ark)</Select.Option>
          </Select>
        </Form.Item>
        <Form.Item label="Base URL">
          <Input
            value={(cfg.base_url as string) || ''}
            onChange={(e) => setField('base_url', e.target.value)}
            placeholder="https://ark.cn-beijing.volces.com/api/v3/images/generations"
          />
        </Form.Item>
        <Form.Item label="Model">
          <Input
            value={(cfg.model as string) || ''}
            onChange={(e) => setField('model', e.target.value)}
            placeholder="doubao-seedream-5-0-260128"
          />
        </Form.Item>
        <Form.Item label="Size">
          <Input
            value={(cfg.size as string) || ''}
            onChange={(e) => setField('size', e.target.value)}
            placeholder="e.g. 2K (仅火山引擎有效)"
          />
        </Form.Item>
        <Form.Item label="API Key (Bearer Token)">
          <Input.Password
            placeholder="Leave empty to keep current"
            onChange={(e) => setField('api_key', e.target.value)}
          />
        </Form.Item>
        <Form.Item label="Access Key ID (AK)">
          <Input.Password
            placeholder="Leave empty to keep current"
            onChange={(e) => setField('access_key_id', e.target.value)}
          />
        </Form.Item>
        <Form.Item label="Secret Access Key (SK)">
          <Input.Password
            placeholder="Leave empty to keep current"
            onChange={(e) => setField('secret_access_key', e.target.value)}
          />
        </Form.Item>
        <Form.Item label="Session Token (临时凭证需要)">
          <Input.Password
            placeholder="Leave empty to keep current"
            onChange={(e) => setField('session_token', e.target.value)}
          />
        </Form.Item>
        <Row gutter={16}>
          <Col span={12}>
            <Form.Item label="Default Width">
              <InputNumber
                min={64}
                max={2048}
                value={(cfg.default_width as number) ?? 1024}
                onChange={(v) => setField('default_width', v)}
                style={{ width: '100%' }}
              />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item label="Default Height">
              <InputNumber
                min={64}
                max={2048}
                value={(cfg.default_height as number) ?? 1024}
                onChange={(v) => setField('default_height', v)}
                style={{ width: '100%' }}
              />
            </Form.Item>
          </Col>
        </Row>
        <Button type="primary" onClick={() => saveConfigJson(cfg)} loading={saving}>
          {t('settings.saveConfig')}
        </Button>
      </Form>
    );
  }

  function renderContent() {
    if (!group) return <div>Not found</div>;
    if (group.config_type === 'model_service') return renderModelServiceForm();
    if (group.config_type === 'agent') return renderAgentForm();
    if (group.config_type === 'image_generation') return renderImageGenerationForm();
    return <div>Unknown config type</div>;
  }

  return (
    <div>
      <div style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 12 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/settings')}>
          {t('btn.back')}
        </Button>
        <h2 style={{ margin: 0 }}>
          {t('settings.editConfig')} - {group?.name || id}
        </h2>
      </div>

      <Card loading={loading}>
        {group && (
          <div style={{ marginBottom: 16 }}>
            <Space>
              <Badge
                status={group.enabled ? 'success' : 'default'}
                text={group.enabled ? t('settings.enabled') : t('settings.disabled')}
              />
              <Tag color={group.config_type === 'model_service' ? 'purple' : group.config_type === 'image_generation' ? 'orange' : 'cyan'}>
                {group.config_type === 'model_service' ? t('settings.modelServiceType') : group.config_type === 'image_generation' ? t('settings.imageGenType') : t('settings.agentType')}
              </Tag>
              <span style={{ color: '#888' }}>Key: {group.key}</span>
            </Space>
            <p style={{ color: '#888', marginTop: 8 }}>{group.description}</p>
          </div>
        )}
        {renderContent()}
      </Card>
    </div>
  );
}
