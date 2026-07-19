import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { Card, List, Switch, Tag, Button, Space, Skeleton, Popconfirm, Modal, Form, Input, Select } from 'antd';
import { EditOutlined, PlusOutlined, DeleteOutlined, ApiOutlined } from '@ant-design/icons';
import { adminFetchJson, adminFetch, showSuccess, showError } from '../../api/request';
import type { ConfigGroup } from '../../types';

export default function SettingsList() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [groups, setGroups] = useState<ConfigGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const [testStatuses, setTestStatuses] = useState<Record<number, 'unknown' | 'testing' | 'connected' | 'disconnected'>>({});

  const loadGroups = useCallback(async () => {
    setLoading(true);
    try {
      const res = await adminFetchJson<ConfigGroup[]>('/api/admin/config-groups');
      if (res) setGroups(res);
    } catch {
      showError(t('toast.loadFailed') as string);
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadGroups();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadGroups]);

  async function toggleEnabled(group: ConfigGroup, checked: boolean) {
    try {
      const res = await adminFetch(`/api/admin/config-groups/${group.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: checked }),
      });
      if (res && res.ok) {
        showSuccess(t('toast.saved') as string);
        setGroups((prev) =>
          prev.map((g) => (g.id === group.id ? { ...g, enabled: checked } : g))
        );
      } else {
        showError(t('settings.saveFailed') as string);
      }
    } catch {
      showError(t('settings.saveFailed') as string);
    }
  }

  async function handleDelete(group: ConfigGroup) {
    try {
      const res = await adminFetch(`/api/admin/config-groups/${group.id}`, { method: 'DELETE' });
      if (res && res.ok) {
        showSuccess(t('toast.deleteSuccess') as string);
        setGroups((prev) => prev.filter((g) => g.id !== group.id));
      } else {
        showError(t('settings.saveFailed') as string);
      }
    } catch {
      showError(t('settings.saveFailed') as string);
    }
  }

  async function handleCreate(values: { key: string; name: string; description: string; config_type: string }) {
    setSaving(true);
    try {
      const res = await adminFetch('/api/admin/config-groups', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(values),
      });
      if (res && res.ok) {
        const data = await res.json();
        showSuccess(t('toast.saved') as string);
        setModalOpen(false);
        form.resetFields();
        void loadGroups();
        // 创建成功后跳转到编辑页
        navigate(`/settings/${data.id}/edit`);
      } else {
        const err = await res?.text();
        showError(err || (t('settings.saveFailed') as string));
      }
    } catch {
      showError(t('settings.saveFailed') as string);
    } finally {
      setSaving(false);
    }
  }

  async function testConnection(group: ConfigGroup) {
    setTestStatuses((prev) => ({ ...prev, [group.id]: 'testing' }));
    try {
      const res = await adminFetchJson<{ ok: boolean; response?: string; error?: string }>(`/api/admin/config-groups/${group.id}/test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      if (res?.ok) {
        setTestStatuses((prev) => ({ ...prev, [group.id]: 'connected' }));
        showSuccess(`${t('toast.testSuccess')}: ${res.response}`);
      } else {
        setTestStatuses((prev) => ({ ...prev, [group.id]: 'disconnected' }));
        showError(`${t('toast.testFailed')}: ${res?.error || ''}`);
      }
    } catch {
      setTestStatuses((prev) => ({ ...prev, [group.id]: 'disconnected' }));
      showError(t('toast.testError') as string);
    }
  }

  function getConnectionTag(group: ConfigGroup) {
    const status = testStatuses[group.id] || 'unknown';
    if (status === 'connected') {
      return <Tag color="success">{t('settings.statusConnected')}</Tag>;
    }
    if (status === 'disconnected') {
      return <Tag color="error">{t('settings.statusDisconnected')}</Tag>;
    }
    if (status === 'testing') {
      return <Tag color="processing">{t('settings.statusTesting')}</Tag>;
    }
    return <Tag>{t('settings.statusUnknown')}</Tag>;
  }

  function getConfigSummary(group: ConfigGroup) {
    const cfg = group.config_json || {};
    if (group.config_type === 'model_service') {
      const provider = (cfg.model_provider as string) || '--';
      const keys: string[] = [];
      if (cfg.anthropic_ready) keys.push('Anthropic');
      if (cfg.deepseek_ready) keys.push('DeepSeek');
      if (cfg.openai_ready) keys.push('OpenAI');
      return (
        <Space size="small" wrap>
          <Tag>{provider}</Tag>
          {keys.map((k) => (
            <Tag key={k} color="success">{k}</Tag>
          ))}
        </Space>
      );
    }
    if (group.config_type === 'agent') {
      const temp = (cfg.temperature as number) ?? 0.93;
      const tokens = (cfg.max_tokens as number) ?? 2048;
      const provider = (cfg.model_provider as string) || t('settings.followSystem');
      return (
        <Space size="small" wrap>
          <Tag color="blue">Temp: {temp}</Tag>
          <Tag color="blue">Max: {tokens}</Tag>
          <Tag>{provider}</Tag>
        </Space>
      );
    }
    if (group.config_type === 'image_generation') {
      const provider = (cfg.provider as string) || '--';
      const model = (cfg.model as string) || '--';
      return (
        <Space size="small" wrap>
          <Tag color="orange">{provider}</Tag>
          <Tag>{model}</Tag>
        </Space>
      );
    }
    return null;
  }

  const typeColor: Record<string, string> = {
    model_service: 'purple',
    agent: 'cyan',
    image_generation: 'orange',
  };

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2>{t('page.settings')}</h2>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
          {t('settings.newConfig')}
        </Button>
      </div>

      <Card style={{ marginTop: 16 }}>
        {loading ? (
          <Skeleton active paragraph={{ rows: 4 }} />
        ) : (
          <List
            dataSource={groups}
            renderItem={(group) => (
              <List.Item
                actions={[
                  <Switch
                    key="enable"
                    checked={group.enabled}
                    onChange={(checked) => toggleEnabled(group, checked)}
                    checkedChildren={t('yes')}
                    unCheckedChildren={t('no')}
                  />,
                  <Button
                    key="test"
                    icon={<ApiOutlined />}
                    onClick={() => testConnection(group)}
                    loading={testStatuses[group.id] === 'testing'}
                  >
                    {t('settings.testConnectionBtn')}
                  </Button>,
                  <Button
                    key="edit"
                    type="primary"
                    icon={<EditOutlined />}
                    onClick={() => navigate(`/settings/${group.id}/edit`)}
                  >
                    {t('btn.edit')}
                  </Button>,
                  <Popconfirm
                    key="delete"
                    title={t('settings.deleteConfirm')}
                    onConfirm={() => handleDelete(group)}
                    okText={t('yes')}
                    cancelText={t('no')}
                  >
                    <Button danger icon={<DeleteOutlined />}>
                      {t('btn.delete')}
                    </Button>
                  </Popconfirm>,
                ]}
              >
                <List.Item.Meta
                  title={
                    <Space>
                      <span>{group.name}</span>
                      <Tag color={typeColor[group.config_type] || 'default'}>
                        {group.config_type === 'model_service'
                          ? t('settings.modelServiceType')
                          : group.config_type === 'image_generation'
                            ? t('settings.imageGenType')
                            : t('settings.agentType')}
                      </Tag>
                      {group.enabled ? (
                        <Tag color="success">{t('settings.enabled')}</Tag>
                      ) : (
                        <Tag>{t('settings.disabled')}</Tag>
                      )}
                      {getConnectionTag(group)}
                    </Space>
                  }
                  description={
                    <Space direction="vertical" size={0} style={{ width: '100%' }}>
                      <span style={{ color: '#888' }}>{group.description}</span>
                      <div style={{ marginTop: 4 }}>{getConfigSummary(group)}</div>
                    </Space>
                  }
                />
              </List.Item>
            )}
          />
        )}
      </Card>

      <Modal
        title={t('settings.newConfig')}
        open={modalOpen}
        onCancel={() => { setModalOpen(false); form.resetFields(); }}
        onOk={() => form.submit()}
        confirmLoading={saving}
      >
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="key" label={t('settings.configKey')} rules={[{ required: true }]}>
            <Input placeholder="e.g. my_agent_config" />
          </Form.Item>
          <Form.Item name="name" label={t('table.name')} rules={[{ required: true }]}>
            <Input placeholder="配置名称" />
          </Form.Item>
          <Form.Item name="config_type" label={t('settings.configType')} rules={[{ required: true }]} initialValue="agent">
            <Select>
              <Select.Option value="model_service">{t('settings.modelServiceType')}</Select.Option>
              <Select.Option value="agent">{t('settings.agentType')}</Select.Option>
              <Select.Option value="image_generation">{t('settings.imageGenType')}</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item name="description" label={t('table.summary')}>
            <Input.TextArea rows={2} placeholder="配置描述..." />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
