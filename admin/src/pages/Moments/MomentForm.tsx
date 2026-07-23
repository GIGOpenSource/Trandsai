import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router-dom';
import { Form, Input, Button, Space, Card } from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';
import { adminFetchJson, adminFetch, showSuccess, showError } from '../../api/request';

interface MomentData {
  id: number;
  companion_id: string;
  caption: string;
  image_url: string | null;
}

export default function MomentForm() {
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
      const listRes = await adminFetchJson<{ moments: MomentData[] }>('/api/admin/moments?limit=1000');
      const item = listRes?.moments?.find((m) => String(m.id) === id);
      if (item) {
        form.setFieldsValue({
          companion_id: item.companion_id,
          caption: item.caption,
          image_url: item.image_url || '',
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
        res = await adminFetch(`/api/admin/moments/${id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(values),
        });
      } else {
        res = await adminFetch('/api/admin/moments', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(values),
        });
      }
      if (res && res.ok) {
        showSuccess(t('toast.saved') as string);
        navigate('/moments');
      } else {
        showError(t('settings.saveFailed') as string);
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
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/moments')}>
          {t('btn.back')}
        </Button>
        <h2 style={{ margin: 0 }}>{isEdit ? t('btn.edit') + ' ' + t('page.moments') : t('btn.new') + ' ' + t('page.moments')}</h2>
      </div>

      <Card loading={loading}>
        <Form form={form} layout="vertical">
          <Form.Item name="companion_id" label={t('table.companion')} rules={[{ required: true }]}>
            <Input placeholder="Companion ID" />
          </Form.Item>
          <Form.Item name="caption" label={t('table.caption')} rules={[{ required: true }]}>
            <Input.TextArea rows={4} />
          </Form.Item>
          <Form.Item name="image_url" label={t('table.image')}>
            <Input placeholder="https://..." />
          </Form.Item>
          <Space>
            <Button type="primary" onClick={handleSave} loading={saving}>
              {t('btn.save')}
            </Button>
            <Button onClick={() => navigate('/moments')}>
              {t('btn.cancel')}
            </Button>
          </Space>
        </Form>
      </Card>
    </div>
  );
}
