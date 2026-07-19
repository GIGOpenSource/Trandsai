import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import ProTable, { type ActionType } from '@ant-design/pro-table';
import { Button, Popconfirm, Space, Tag } from 'antd';
import { PlusOutlined, DeleteOutlined } from '@ant-design/icons';
import { adminFetchJson, adminFetch, showSuccess, showError, formatDate } from '../../api/request';
import type { SystemNotificationItem } from '../../types';
import { getAppLanguage } from '../../locales';

const LANGUAGE_MAP: Record<string, string> = {
  zh: '中文',
  en: 'English',
  ja: '日本語',
  ko: '한국어',
  pt: 'Português',
  es: 'Español',
  id: 'Bahasa Indonesia',
};

export default function NotificationsList() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const actionRef = useRef<ActionType | null>(null);
  const locale = getAppLanguage();
  const [selectedRowKeys, setSelectedRowKeys] = useState<number[]>([]);

  const columns = [
    { title: t('table.id'), dataIndex: 'id', key: 'id', width: 60 },
    {
      title: t('table.title'),
      dataIndex: 'title',
      key: 'title',
      width: 200,
      ellipsis: true,
    },
    {
      title: t('table.content'),
      dataIndex: 'content',
      key: 'content',
      ellipsis: true,
    },
    {
      title: t('table.language'),
      dataIndex: 'language',
      key: 'language',
      width: 120,
      render: (_: unknown, record: SystemNotificationItem) => (
        <Tag>{LANGUAGE_MAP[record.language] || record.language}</Tag>
      ),
    },
    {
      title: t('table.createdAt'),
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (_: unknown, record: SystemNotificationItem) => formatDate(record.created_at, locale),
    },
    {
      title: t('table.action'),
      key: 'action',
      width: 120,
      render: (_: unknown, record: SystemNotificationItem) => (
        <Space>
          <Popconfirm
            title={t('modal.confirmDelete') || '确定删除此通知？'}
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
      const res = await adminFetchJson<SystemNotificationItem[]>('/api/admin/notifications');
      return { 
        data: res || [], 
        success: true, 
        total: (res || []).length 
      };
    } catch {
      showError(t('toast.loadFailed') as string);
      return { data: [], success: false, total: 0 };
    }
  }

  async function handleDelete(id: number) {
    try {
      const res = await adminFetch(`/api/admin/notifications/${id}`, { method: 'DELETE' });
      if (res && res.ok) {
        showSuccess(t('toast.deleteSuccess') as string);
        actionRef.current?.reload();
        // 清空选择如果被删除
        setSelectedRowKeys(prev => prev.filter(key => key !== id));
      } else {
        showError(t('toast.loadFailed') as string);
      }
    } catch {
      showError(t('toast.loadFailed') as string);
    }
  }

  async function handleBatchDelete() {
    if (selectedRowKeys.length === 0) return;
    try {
      const res = await adminFetch('/api/admin/notifications/batch-delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: selectedRowKeys }),
      });
      if (res && res.ok) {
        const result = await res.json();
        showSuccess(`成功删除 ${result.deleted || selectedRowKeys.length} 条通知`);
        setSelectedRowKeys([]);
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
      <ProTable<SystemNotificationItem>
        actionRef={actionRef}
        columns={columns}
        rowKey="id"
        request={fetchData}
        search={false}
        pagination={{ pageSize: 10 }}
        rowSelection={{
          selectedRowKeys,
          onChange: (keys) => setSelectedRowKeys(keys as number[]),
        }}
        toolBarRender={() => [
          <Button
            key="new"
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => navigate('/notifications/new')}
          >
            {t('btn.new')}
          </Button>,
          selectedRowKeys.length > 0 && (
            <Popconfirm
              key="batch-delete"
              title={`确定删除选中的 ${selectedRowKeys.length} 条通知吗？此操作不可恢复！`}
              onConfirm={handleBatchDelete}
              okText={t('btn.delete')}
              cancelText={t('btn.cancel')}
              okButtonProps={{ danger: true }}
            >
              <Button danger icon={<DeleteOutlined />}>
                批量删除 ({selectedRowKeys.length})
              </Button>
            </Popconfirm>
          ),
        ]}
      />
    </div>
  );
}
