import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { Form, Input, Button, Space, Card, Select } from 'antd';
import { ArrowLeftOutlined, SendOutlined } from '@ant-design/icons';
import { adminFetchJson, showSuccess, showError } from '../../api/request';

const LANGUAGE_OPTIONS = [
  { value: 'zh', label: '中文' },
  { value: 'en', label: 'English' },
  { value: 'ja', label: '日本語' },
  { value: 'ko', label: '한국어' },
  { value: 'pt', label: 'Português' },
  { value: 'es', label: 'Español' },
  { value: 'id', label: 'Bahasa Indonesia' },
];

type NotificationCreateResponse = {
  ok?: boolean;
  id?: number;
};

export default function NotificationForm() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    const values = await form.validateFields();
    setSaving(true);
    try {
      // 使用 adminFetchJson 统一错误解析，支持 {error} 或 {detail}，避免 null/401 运行时错误
      const data = await adminFetchJson<NotificationCreateResponse>('/api/admin/notifications', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(values),
      });
      if (data && (data.id || data.ok)) {
        showSuccess(t('toast.saved') as string);
        navigate('/notifications');
      } else {
        showError(t('toast.savedFailed') as string);  // 使用统一提示
      }
    } catch (err: unknown) {
      // 改进错误显示，使用解析后的错误信息
      const msg = err instanceof Error ? err.message : (t('settings.saveFailed') as string);
      showError(msg);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <div style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 12 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/notifications')}>
          {t('btn.back')}
        </Button>
        <h2 style={{ margin: 0 }}>{t('btn.new') + ' ' + (t('nav.notifications') || '系统通知')}</h2>
      </div>

      <Card>
        <Form form={form} layout="vertical">
          <Form.Item
            name="title"
            label={t('table.title')}
            rules={[{ required: true, message: t('validation.required') || '请输入标题' }]}
          >
            <Input placeholder={t('modal.enterTitle') || '请输入通知标题'} maxLength={200} showCount />
          </Form.Item>

          <Form.Item
            name="content"
            label={t('table.content')}
            rules={[{ required: true, message: t('validation.required') || '请输入内容' }]}
          >
            <Input.TextArea
              rows={6}
              placeholder={t('modal.enterContent') || '请输入通知内容'}
              maxLength={2000}
              showCount
            />
          </Form.Item>

          <Form.Item
            name="language"
            label={t('table.language') || '语言'}
            initialValue="zh"
            rules={[{ required: true, message: t('validation.required') || '请选择语言' }]}
          >
            <Select
              placeholder={t('modal.selectLanguage') || '请选择语言'}
              options={LANGUAGE_OPTIONS}
              style={{ width: 200 }}
            />
          </Form.Item>

          <Space>
            <Button type="primary" icon={<SendOutlined />} onClick={handleSave} loading={saving}>
              {t('btn.send') || '发送'}
            </Button>
            <Button onClick={() => navigate('/notifications')}>
              {t('btn.cancel')}
            </Button>
          </Space>
        </Form>
      </Card>
    </div>
  );
}
