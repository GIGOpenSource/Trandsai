import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router-dom';
import { Form, Input, InputNumber, Select, Button, Space, Card } from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';
import { adminFetchJson, adminFetch, showSuccess, showError } from '../../api/request';
import type { CompanionItem } from '../../types';

const LANGUAGES = [
  { k: 'zh', l: '中文' },
  { k: 'en', l: 'English' },
  { k: 'ja', l: '日本語' },
  { k: 'ko', l: '한국어' },
  { k: 'pt', l: 'Português' },
  { k: 'es', l: 'Español' },
  { k: 'id', l: 'Bahasa Indonesia' },
];

export default function CompanionForm() {
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
      const res = await adminFetchJson<CompanionItem>(`/api/admin/companions/${id}`);
      if (res) {
        const promptRecord = res as CompanionItem & Record<string, string | undefined>;
        form.setFieldsValue({
          ...res.profile,
          ...Object.fromEntries(
            LANGUAGES.map((lang) => [
              `system_prompt_${lang.k}`,
              promptRecord[`system_prompt_${lang.k}`] || '',
            ])
          ),
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
        res = await adminFetch(`/api/admin/companions/${id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(values),
        });
      } else {
        res = await adminFetch('/api/admin/companions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(values),
        });
      }
      if (res && res.ok) {
        showSuccess(t('toast.saved') as string);
        navigate('/companions');
      } else {
        showError(t('settings.saveFailed') as string);
      }
    } catch (err: unknown) {
      // 使用改进的错误解析（来自 request.ts 更新）
      const msg = err instanceof Error ? err.message : t('settings.saveFailed');
      showError(msg as string);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <div style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 12 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/companions')}>
          {t('btn.back')}
        </Button>
        <h2 style={{ margin: 0 }}>{isEdit ? t('btn.edit') + ' ' + t('page.companions') : t('btn.new') + ' ' + t('page.companions')}</h2>
      </div>

      <Card loading={loading}>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label={t('table.name')} rules={[{ required: true }]}>
            <Input maxLength={20} />
          </Form.Item>
          <Form.Item name="gender" label={t('table.gender')} rules={[{ required: true }]}>
            <Select>
              <Select.Option value="女">女</Select.Option>
              <Select.Option value="男">男</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item name="age" label={t('table.age')} rules={[{ required: true }]}>
            <InputNumber min={18} max={99} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="city" label={t('table.city')} rules={[{ required: true }]}>
            <Input maxLength={20} />
          </Form.Item>
          <Form.Item name="personality" label="性格描述">
            <Input.TextArea rows={3} maxLength={500} />
          </Form.Item>
          <Form.Item name="background" label="背景故事">
            <Input.TextArea rows={3} maxLength={1000} />
          </Form.Item>
          <Form.Item name="speech_style" label="说话风格">
            <Input.TextArea rows={3} maxLength={500} />
          </Form.Item>
          <Form.Item name="hobbies" label="兴趣爱好">
            <Input.TextArea rows={2} maxLength={500} />
          </Form.Item>
          <Form.Item name="values" label="核心价值观">
            <Input.TextArea rows={2} maxLength={500} />
          </Form.Item>
          <Form.Item name="fears" label="内心脆弱点">
            <Input.TextArea rows={2} maxLength={500} />
          </Form.Item>
          <Form.Item name="love_view" label="恋爱观">
            <Input.TextArea rows={2} maxLength={500} />
          </Form.Item>
          <Form.Item name="daily_routine" label="典型一天">
            <Input.TextArea rows={2} maxLength={500} />
          </Form.Item>
          <Form.Item name="favorite_things" label="喜欢的东西">
            <Input.TextArea rows={2} maxLength={500} />
          </Form.Item>
          <h4>{t('modal.systemPrompt')}</h4>
          {LANGUAGES.map((lang) => (
            <Form.Item key={lang.k} name={`system_prompt_${lang.k}`} label={lang.l}>
              <Input.TextArea rows={3} placeholder={t('settings.emptyUseDefault') as string} />
            </Form.Item>
          ))}
          <Space>
            <Button type="primary" onClick={handleSave} loading={saving}>
              {t('btn.save')}
            </Button>
            <Button onClick={() => navigate('/companions')}>
              {t('btn.cancel')}
            </Button>
          </Space>
        </Form>
      </Card>
    </div>
  );
}
