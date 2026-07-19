import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router-dom';
import { Form, Input, Select, Button, Space, Card } from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';
import { adminFetchJson, adminFetch, showSuccess, showError } from '../../api/request';
import type { KnowledgeEntry } from '../../types';

export default function KnowledgeForm() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { id } = useParams<{ id: string }>();
  const isEdit = !!id;
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const loadData = useCallback(async () => {
    if (!isEdit || !id) return;
    setLoading(true);
    try {
      const res = await adminFetchJson<KnowledgeEntry[]>('/api/admin/knowledge');
      const item = res?.find((k) => k.id === id);
      if (item) {
        form.setFieldsValue({
          title: item.title,
          category: item.category,
          language: item.language,
          content: item.content,
        });
      }
    } catch {
      showError(t('toast.loadFailed') as string);
    } finally {
      setLoading(false);
    }
  }, [form, id, isEdit, t]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadData();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadData]);

  async function handleSave() {
    const values = await form.validateFields();
    setSaving(true);
    try {
      let res: Response | null;
      if (isEdit) {
        res = await adminFetch(`/api/admin/knowledge/${id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(values),
        });
      } else {
        res = await adminFetch('/api/admin/knowledge', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(values),
        });
      }
      if (res && res.ok) {
        showSuccess(t('toast.saved') as string);
        navigate('/knowledge');
      } else {
        const err = await res?.text();
        showError(err || t('settings.saveFailed') as string);
      }
    } catch {
      showError(t('settings.saveFailed') as string);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <div style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 12 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/knowledge')}>
          {t('btn.back')}
        </Button>
        <h2 style={{ margin: 0 }}>{isEdit ? t('btn.edit') + ' ' + t('page.knowledge') : t('btn.new') + ' ' + t('page.knowledge')}</h2>
      </div>

      <Card loading={loading}>
        <Form form={form} layout="vertical">
          <Form.Item name="title" label={t('table.title')} rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="category" label={t('table.category')} rules={[{ required: true }]}>
            <Select placeholder="选择分类">
              <Select.Option value="pua_tactics">pua_tactics</Select.Option>
              <Select.Option value="red_flags">red_flags</Select.Option>
              <Select.Option value="love_bombing">love_bombing</Select.Option>
              <Select.Option value="gaslighting">gaslighting</Select.Option>
              <Select.Option value="breadcrumbing">breadcrumbing</Select.Option>
              <Select.Option value="narcissist">narcissist</Select.Option>
              <Select.Option value="emotional_blackmail">emotional_blackmail</Select.Option>
              <Select.Option value="other">other</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item name="language" label={t('table.language')} rules={[{ required: true }]}>
            <Select placeholder="选择语言">
              <Select.Option value="zh">中文</Select.Option>
              <Select.Option value="en">English</Select.Option>
              <Select.Option value="ja">日本語</Select.Option>
              <Select.Option value="ko">한국어</Select.Option>
              <Select.Option value="pt">Português</Select.Option>
              <Select.Option value="es">Español</Select.Option>
              <Select.Option value="id">Bahasa Indonesia</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item name="content" label={t('table.summary')} rules={[{ required: true }]}>
            <Input.TextArea rows={8} />
          </Form.Item>
          <Space>
            <Button type="primary" onClick={handleSave} loading={saving}>
              {t('btn.save')}
            </Button>
            <Button onClick={() => navigate('/knowledge')}>
              {t('btn.cancel')}
            </Button>
          </Space>
        </Form>
      </Card>
    </div>
  );
}
