import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router-dom';
import { Form, Input, Button, Space, Card, message } from 'antd';
import { ArrowLeftOutlined, SendOutlined } from '@ant-design/icons';
import { adminFetchJson, adminFetch, showSuccess, showError, formatDate } from '../../api/request';
import type { FeedbackThreadDetail, FeedbackMessage } from '../../types';
import { getAppLanguage } from '../../locales';

type FeedbackCreateResponse = {
  ok?: boolean;
  id?: number;
};

export default function FeedbackForm() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { id } = useParams<{ id: string }>();
  const isEdit = !!id;
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [threadDetail, setThreadDetail] = useState<FeedbackThreadDetail | null>(null);
  const [replyContent, setReplyContent] = useState('');
  const locale = getAppLanguage();
  const loadData = useCallback(async () => {
    if (!isEdit || !id) return;
    setLoading(true);
    try {
      const res = await adminFetchJson<FeedbackThreadDetail>(`/api/admin/feedback/${id}/messages`);
      if (res) {
        setThreadDetail(res);
      }
    } catch {
      showError(t('toast.loadFailed') as string);
    } finally {
      setLoading(false);
    }
  }, [id, isEdit, t]);

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
      // 新建反馈：现在后端已实现 POST /api/admin/feedback，支持管理员创建线程（优化后）
      const data = await adminFetchJson<FeedbackCreateResponse>('/api/admin/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(values),
      });
      if (data && (data.ok || data.id)) {
        showSuccess(t('toast.saved') as string);
        navigate('/feedback');
      } else {
        showError(t('settings.saveFailed') as string);
      }
    } catch (err: unknown) {
      // 使用改进的错误解析
      const msg = err instanceof Error ? err.message : (t('settings.saveFailed') as string);
      showError(msg);
    } finally {
      setSaving(false);
    }
  }

  async function handleReply() {
    if (!replyContent.trim()) {
      message.warning(t('toast.enterContent'));
      return;
    }
    if (!threadDetail) return;
    try {
      const res = await adminFetch(`/api/admin/feedback/${threadDetail.thread.id}/reply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: replyContent }),
      });
      if (res && res.ok) {
        showSuccess(t('toast.sent') as string);
        setReplyContent('');
        void loadData();
      } else {
        showError(t('toast.sendFailed') as string);
      }
    } catch {
      showError(t('toast.sendFailed') as string);
    }
  }

  if (isEdit && !threadDetail && !loading) {
    return (
      <div>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/feedback')}>
          {t('btn.back')}
        </Button>
        <p style={{ marginTop: 24 }}>{t('empty.data')}</p>
      </div>
    );
  }

  return (
    <div>
      <div style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 12 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/feedback')}>
          {t('btn.back')}
        </Button>
        <h2 style={{ margin: 0 }}>
          {isEdit ? `${t('modal.feedbackDetail')} #${id}` : t('btn.new') + ' ' + t('page.feedback')}
        </h2>
      </div>

      {isEdit && threadDetail ? (
        <Card loading={loading}>
          <div style={{ marginBottom: 12, fontSize: 13, color: '#666' }}>
            {t('table.user')}: {threadDetail.thread.user_name || '-'} (ID: {threadDetail.thread.user_id}) | {' '}
            {t('table.status')}: {threadDetail.thread.status === 'replied' ? t('status.replied') : t('status.pending')}
          </div>
          <div
            style={{
              maxHeight: 360,
              overflowY: 'auto',
              marginBottom: 16,
              border: '1px solid #eee',
              borderRadius: 8,
              padding: 12,
              background: '#fafafa',
            }}
          >
            {threadDetail.messages.length === 0 && (
              <p style={{ textAlign: 'center', color: '#999' }}>{t('empty.messages')}</p>
            )}
            {threadDetail.messages.map((m: FeedbackMessage) => (
              <div
                key={m.id}
                style={{
                  marginBottom: 10,
                  textAlign: m.sender === 'user' ? 'right' : 'left',
                }}
              >
                <div style={{ fontSize: 12, color: '#999', marginBottom: 2 }}>
                  {(m.sender === 'user' ? '用户' : m.sender === 'admin' ? '管理员' : '系统')} · {formatDate(m.created_at, locale)}
                </div>
                <div
                  style={{
                    display: 'inline-block',
                    padding: '8px 12px',
                    borderRadius: 12,
                    background: m.sender === 'user' ? '#e94560' : m.sender === 'admin' ? '#4a69bd' : '#555',
                    color: '#fff',
                    maxWidth: '80%',
                    wordBreak: 'break-word',
                    textAlign: 'left',
                  }}
                >
                  {m.content}
                </div>
              </div>
            ))}
          </div>
          <Space.Compact style={{ width: '100%' }}>
            <Input.TextArea
              rows={3}
              value={replyContent}
              onChange={(e) => setReplyContent(e.target.value)}
              placeholder={t('modal.replyPlaceholder') as string}
            />
            <Button type="primary" icon={<SendOutlined />} onClick={handleReply}>
              {t('modal.sendReply')}
            </Button>
          </Space.Compact>
        </Card>
      ) : (
        <Card loading={loading}>
          <Form form={form} layout="vertical">
            <Form.Item name="user_name" label="用户名称" rules={[{ required: true }]}>
              <Input />
            </Form.Item>
            <Form.Item name="content" label="反馈内容" rules={[{ required: true }]}>
              <Input.TextArea rows={4} />
            </Form.Item>
            <Space>
              <Button type="primary" onClick={handleSave} loading={saving}>
                {t('btn.save')}
              </Button>
              <Button onClick={() => navigate('/feedback')}>
                {t('btn.cancel')}
              </Button>
            </Space>
          </Form>
        </Card>
      )}
    </div>
  );
}
