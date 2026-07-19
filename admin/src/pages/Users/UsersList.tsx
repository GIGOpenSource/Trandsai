import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import ProTable, { type ActionType } from '@ant-design/pro-table';
import { Button, Popconfirm, Space } from 'antd';
import { EditOutlined, DeleteOutlined, PlusOutlined } from '@ant-design/icons';
import { adminFetchJson, adminFetch, showSuccess, showError, formatDate } from '../../api/request';
import type { UserItem } from '../../types';
import { getAppLanguage } from '../../locales';

export default function UsersList() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const actionRef = useRef<ActionType | null>(null);
  const locale = getAppLanguage();

  const [selectedRowKeys, setSelectedRowKeys] = useState<(string | number)[]>([]);

  const columns = [
    { title: t('table.id'), dataIndex: 'id', key: 'id', width: 80 },
    { title: t('table.username'), dataIndex: 'username', key: 'username' },
    {
      title: t('table.nickname'),
      dataIndex: 'nickname',
      key: 'nickname',
      render: (_: unknown, record: UserItem) => record.nickname || '-',
    },
    {
      title: t('table.gender'),
      dataIndex: 'gender',
      key: 'gender',
      width: 80,
      render: (_: unknown, record: UserItem) => record.gender || '-',
    },
    {
      title: t('table.sexualOrientation') || '性取向',
      dataIndex: 'sexual_orientation',
      key: 'sexual_orientation',
      width: 100,
      render: (_: unknown, record: UserItem) => record.sexual_orientation || '-',
    },
    {
      title: t('table.age'),
      dataIndex: 'age',
      key: 'age',
      width: 64,
      render: (_: unknown, record: UserItem) =>
        record.age !== undefined && record.age !== null ? record.age : '-',
    },
    {
      title: t('table.region'),
      dataIndex: 'region',
      key: 'region',
      width: 100,
      ellipsis: true,
      render: (_: unknown, record: UserItem) => record.region || '-',
    },
    {
      title: t('table.occupation'),
      dataIndex: 'occupation',
      key: 'occupation',
      width: 100,
      ellipsis: true,
      render: (_: unknown, record: UserItem) => record.occupation || '-',
    },
    {
      title: t('table.createdAt'),
      dataIndex: 'created_at',
      key: 'created_at',
      render: (_: unknown, record: UserItem) => formatDate(record.created_at, locale),
    },
    {
      title: t('table.action'),
      key: 'action',
      width: 180,
      render: (_: unknown, record: UserItem) => (
        <Space>
          <Button
            type="link"
            icon={<EditOutlined />}
            onClick={() => navigate(`/users/${record.id}/edit`)}
          >
            {t('btn.edit')}
          </Button>
          <Popconfirm
            title={t('confirm.deleteUser') || '确定删除此用户？'}
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
      const res = await adminFetchJson<UserItem[]>('/api/admin/users');
      return { data: res || [], success: true, total: (res || []).length };
    } catch {
      showError(t('toast.loadFailed') as string);
      return { data: [], success: false, total: 0 };
    }
  }

  async function handleDelete(id: number) {
    try {
      const res = await adminFetch(`/api/admin/users/${id}`, { method: 'DELETE' });
      if (res && res.ok) {
        showSuccess(t('toast.deleteSuccess') as string);
        actionRef.current?.reload();
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
      const res = await adminFetch('/api/admin/users/batch-delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: selectedRowKeys }),
      });
      if (res && res.ok) {
        const result = await res.json();
        showSuccess(`成功删除 ${result.deleted || selectedRowKeys.length} 个用户`);
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
      <h2>{t('page.users')}</h2>
      <ProTable
        actionRef={actionRef}
        columns={columns}
        rowKey="id"
        request={fetchData}
        search={false}
        pagination={{ pageSize: 10 }}
        rowSelection={{
          selectedRowKeys,
          onChange: (keys) => setSelectedRowKeys(keys as (string | number)[]),
        }}
        toolBarRender={() => [
          <Button key="new" type="primary" icon={<PlusOutlined />} onClick={() => navigate('/users/new')}>
            {t('btn.new')}
          </Button>,
          selectedRowKeys.length > 0 && (
            <Popconfirm
              key="batch-delete"
              title={`确定删除选中的 ${selectedRowKeys.length} 个用户吗？此操作不可恢复！`}
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
