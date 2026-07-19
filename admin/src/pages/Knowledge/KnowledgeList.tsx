import { useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import ProTable, { type ActionType } from '@ant-design/pro-table';
import { Button, Popconfirm, Space } from 'antd';
import { EditOutlined, DeleteOutlined, PlusOutlined } from '@ant-design/icons';
import { adminFetchJson, adminFetch, showSuccess, showError } from '../../api/request';
import type { KnowledgeEntry } from '../../types';

export default function KnowledgeList() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const actionRef = useRef<ActionType | null>(null);

  const columns = [
    { title: t('table.id'), dataIndex: 'id', key: 'id', width: 100 },
    { title: t('table.title'), dataIndex: 'title', key: 'title' },
    { title: t('table.category'), dataIndex: 'category', key: 'category' },
    { title: t('table.language'), dataIndex: 'language', key: 'language' },
    {
      title: t('table.summary'),
      dataIndex: 'content',
      key: 'content',
      ellipsis: true,
      render: (_: unknown, record: KnowledgeEntry) => (record.content?.length > 80 ? record.content.slice(0, 80) + '...' : record.content || ''),
    },
    {
      title: t('table.action'),
      key: 'action',
      width: 180,
      render: (_: unknown, record: KnowledgeEntry) => (
        <Space>
          <Button
            type="link"
            icon={<EditOutlined />}
            onClick={() => navigate(`/knowledge/${record.id}/edit`)}
          >
            {t('btn.edit')}
          </Button>
          <Popconfirm
            title={t('confirm.deleteMoment')}
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
      const res = await adminFetchJson<KnowledgeEntry[]>('/api/admin/knowledge');
      return { data: res || [], success: true, total: (res || []).length };
    } catch {
      showError(t('toast.loadFailed') as string);
      return { data: [], success: false, total: 0 };
    }
  }

  async function handleDelete(id: string) {
    try {
      const res = await adminFetch(`/api/admin/knowledge/${id}`, { method: 'DELETE' });
      if (res && res.ok) {
        showSuccess(t('toast.deleteSuccess') as string);
        actionRef.current?.reload();
      }
    } catch {
      showError(t('toast.loadFailed') as string);
    }
  }

  return (
    <div>
      <h2>{t('page.knowledge')}</h2>
      <ProTable
        actionRef={actionRef}
        columns={columns}
        rowKey="id"
        request={fetchData}
        search={false}
        pagination={{ pageSize: 10 }}
        toolBarRender={() => [
          <Button key="new" type="primary" icon={<PlusOutlined />} onClick={() => navigate('/knowledge/new')}>
            {t('btn.new')}
          </Button>,
        ]}
      />
    </div>
  );
}
