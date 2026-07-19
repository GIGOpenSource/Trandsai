import { useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import ProTable, { type ActionType } from '@ant-design/pro-table';
import { Button, Popconfirm, Badge, Space } from 'antd';
import { EyeOutlined, DeleteOutlined } from '@ant-design/icons';
import { adminFetchJson, adminFetch, showSuccess, showError, formatDate } from '../../api/request';
import type { FeedbackItem } from '../../types';
import { getAppLanguage } from '../../locales';

export default function FeedbackList() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const actionRef = useRef<ActionType | null>(null);
  const locale = getAppLanguage();

  const columns = [
    { title: t('table.id'), dataIndex: 'id', key: 'id', width: 60 },
    {
      title: t('table.user'),
      key: 'user',
      render: (_: unknown, record: FeedbackItem) => (
        <span>
          {record.user_name || '-'} <span style={{ color: '#999', fontSize: 12 }}>(#{record.user_id})</span>
        </span>
      ),
    },
    {
      title: t('table.lastMessage'),
      key: 'lastMessage',
      render: (_: unknown, record: FeedbackItem) => (
        <span>
          {record.last_message || ''}{' '}
          <span style={{ color: '#999', fontSize: 12 }}>[{record.last_message_sender}]</span>
        </span>
      ),
    },
    {
      title: t('table.status'),
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (_: unknown, record: FeedbackItem) =>
        record.status === 'replied' ? (
          <Badge status="success" text={t('status.replied')} />
        ) : (
          <Badge status="warning" text={t('status.pending')} />
        ),
    },
    {
      title: t('table.updatedAt'),
      dataIndex: 'updated_at',
      key: 'updated_at',
      render: (_: unknown, record: FeedbackItem) => formatDate(record.updated_at, locale),
    },
    {
      title: t('table.action'),
      key: 'action',
      width: 180,
      render: (_: unknown, record: FeedbackItem) => (
        <Space>
          <Button
            type="link"
            icon={<EyeOutlined />}
            onClick={() => navigate(`/feedback/${record.id}/edit`)}
          >
            {t('btn.viewReply')}
          </Button>
          <Popconfirm
            title="确定删除此反馈会话？"
            onConfirm={() => handleDelete(record.id)}
            okText={t('btn.delete')}
            cancelText={t('btn.cancel')}
          >
            <Button type="link" danger icon={<DeleteOutlined />}>
              {t('btn.delete')}
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  async function fetchData() {
    try {
      const res = await adminFetchJson<FeedbackItem[]>('/api/admin/feedback');
      return { data: res || [], success: true, total: (res || []).length };
    } catch {
      showError(t('toast.loadFailed') as string);
      return { data: [], success: false, total: 0 };
    }
  }

  async function handleDelete(id: number) {
    try {
      const res = await adminFetch(`/api/admin/feedback/${id}`, { method: 'DELETE' });
      if (res && res.ok) {
        showSuccess(t('toast.deleteSuccess') as string);
        actionRef.current?.reload();
      } else {
        showError(t('toast.loadFailed') as string);
      }
    } catch {
      showError(t('toast.loadFailed') as string);
    }
  }

  return (
    <div>
      <h2>{t('page.feedback')}</h2>
      <ProTable
        actionRef={actionRef}
        columns={columns}
        rowKey="id"
        request={fetchData}
        search={false}
        pagination={{ pageSize: 10 }}
        toolBarRender={() => []}
      />
    </div>
  );
}
