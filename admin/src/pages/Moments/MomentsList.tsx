import { useState, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import ProTable, { type ActionType } from '@ant-design/pro-table';
import { Button, Popconfirm, Image, Space, message, Modal, Form, InputNumber, Switch, Typography } from 'antd';
import { DeleteOutlined, ReloadOutlined, PlusOutlined, EditOutlined, RobotOutlined, ClearOutlined } from '@ant-design/icons';
import { adminFetchJson, adminFetch, showSuccess, showError, formatDate } from '../../api/request';
import type { MomentItem } from '../../types';
import { getAppLanguage } from '../../locales';
import { resolveAdminMediaUrl } from '../../utils/mediaUrl';

const { Text } = Typography;

export default function MomentsList() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const actionRef = useRef<ActionType | null>(null);
  const [preview, setPreview] = useState<{ url: string; caption: string } | null>(null);
  const [batchModalOpen, setBatchModalOpen] = useState(false);
  const [batchLoading, setBatchLoading] = useState(false);
  const [batchForm] = Form.useForm();
  const locale = getAppLanguage();

  const columns = [
    { title: t('table.id'), dataIndex: 'id', key: 'id', width: 60, fixed: 'left' as const },
    {
      title: t('table.companion'),
      key: 'companion',
      width: 200,
      render: (_: unknown, record: MomentItem) => (
        <Space style={{ maxWidth: '100%' }} align="start">
          {record.companion_avatar ? (
            <Image
              src={resolveAdminMediaUrl(record.companion_avatar)}
              width={32}
              height={32}
              style={{ borderRadius: '50%', objectFit: 'cover', flexShrink: 0 }}
              preview={false}
              fallback="https://api.dicebear.com/7.x/avataaars/svg?seed=fallback"
            />
          ) : null}
          <Text ellipsis style={{ minWidth: 0, maxWidth: 150 }}>
            {record.companion_name || String(record.companion_id)}
          </Text>
        </Space>
      ),
    },
    {
      title: t('table.image'),
      dataIndex: 'image_url',
      key: 'image_url',
      width: 80,
      render: (_: unknown, record: MomentItem) =>
        record.image_generating ? (
          <div style={{ width: 48, height: 48, background: '#f0f0f0', borderRadius: 4, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <div className="animate-spin" style={{ width: 16, height: 16, border: '2px solid #f0f0f0', borderTopColor: '#ff69b4', borderRadius: '50%' }}></div>
          </div>
        ) : record.image_url ? (
          <Image
            src={resolveAdminMediaUrl(record.image_url)}
            width={48}
            height={48}
            style={{ objectFit: 'cover', borderRadius: 4, cursor: 'pointer' }}
            preview={false}
            onClick={() => setPreview({ url: resolveAdminMediaUrl(record.image_url!), caption: record.caption })}
            fallback="https://via.placeholder.com/48x48?text=无图"
          />
        ) : (
          <span style={{ color: '#999' }}>—</span>
        ),
    },
    {
      title: t('table.caption'),
      dataIndex: 'caption',
      key: 'caption',
      width: 260,
      ellipsis: true,
    },
    { title: t('table.likes'), dataIndex: 'likes_count', key: 'likes_count', width: 80 },
    { title: t('table.comments'), dataIndex: 'comments_count', key: 'comments_count', width: 80 },
    {
      title: t('table.publishedAt'),
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (_: unknown, record: MomentItem) => formatDate(record.created_at, locale),
    },
    {
      title: t('table.action'),
      key: 'action',
      width: 300,
      fixed: 'right' as const,
      render: (_: unknown, record: MomentItem) => (
        <Space>
          <Button
            type="link"
            icon={<EditOutlined />}
            onClick={() => navigate(`/moments/${record.id}/edit`)}
          >
            {t('btn.edit')}
          </Button>
          <Button
            type="link"
            icon={<ReloadOutlined />}
            onClick={() => handleRegenerateImage(record.id)}
          >
            {t('btn.regenerateImage')}
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
      const res = await adminFetchJson<{ moments: MomentItem[]; total: number }>('/api/admin/moments?limit=1000');
      return { data: res?.moments || [], success: true, total: res?.total || 0 };
    } catch {
      showError(t('toast.loadFailed') as string);
      return { data: [], success: false, total: 0 };
    }
  }

  async function handleDelete(id: number) {
    try {
      const res = await adminFetch(`/api/admin/moments/${id}`, { method: 'DELETE' });
      if (res && res.ok) {
        showSuccess(t('toast.deleteSuccess') as string);
        actionRef.current?.reload();
      }
    } catch {
      showError(t('toast.loadFailed') as string);
    }
  }

  async function handleRegenerateImage(id: number) {
    try {
      message.loading({ content: t('toast.regenerating'), key: 'img' });
      const res = await adminFetch(`/api/admin/moments/${id}/regenerate-image`, { method: 'POST' });
      if (res && res.ok) {
        message.success({ content: t('toast.regenerateSuccess'), key: 'img' });
        actionRef.current?.reload();
      } else {
        message.error({ content: t('toast.regenerateFailed'), key: 'img' });
      }
    } catch {
      message.error({ content: t('toast.regenerateFailed'), key: 'img' });
    }
  }

  async function handleClearAll() {
    try {
      const res = await adminFetch('/api/admin/moments', { method: 'DELETE' });
      if (res && res.ok) {
        showSuccess(t('toast.clearSuccess') as string);
        actionRef.current?.reload();
      } else {
        showError(t('toast.clearFailed') as string);
      }
    } catch {
      showError(t('toast.clearFailed') as string);
    }
  }

  async function handleBatchGenerate(values: Record<string, unknown>) {
    setBatchLoading(true);
    try {
      message.loading({ content: t('toast.generating'), key: 'batch' });
      const res = await adminFetchJson<{ ok: boolean; created: number; mode: string }>('/api/admin/moments/batch-generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          clear_existing: values.clear_existing,
          moments_per_companion: values.moments_per_companion,
        }),
      });
      if (res && res.ok) {
        message.success({ content: `${t('toast.batchGenerateSuccess') as string} ${res.created} ${t('toast.items') as string}`, key: 'batch' });
        setBatchModalOpen(false);
        batchForm.resetFields();
        actionRef.current?.reload();
      } else {
        message.error({ content: t('toast.batchGenerateFailed'), key: 'batch' });
      }
    } catch (e) {
      message.error({ content: (e as Error).message || (t('toast.batchGenerateFailed') as string), key: 'batch' });
    } finally {
      setBatchLoading(false);
    }
  }

  return (
    <div style={{ width: '100%', minWidth: 0 }}>
      <h2>{t('page.moments')}</h2>
      <ProTable
        actionRef={actionRef}
        columns={columns}
        rowKey="id"
        request={fetchData}
        search={false}
        scroll={{ x: 1280 }}
        /* 有配图生成中的行时自动每 3s 拉取一次，否则后台已写完 URL 但列表不刷新会永远转圈 */
        polling={(dataSource) => {
          const list = (dataSource as MomentItem[]) || [];
          return list.some((m) => m.image_generating) ? 3000 : 0;
        }}
        pagination={{ pageSize: 10 }}
        toolBarRender={() => [
          <Button key="new" type="primary" icon={<PlusOutlined />} onClick={() => navigate('/moments/new')}>
            {t('btn.new')}
          </Button>,
          <Button key="batch" icon={<RobotOutlined />} onClick={() => setBatchModalOpen(true)}>
            {t('btn.batchGenerate')}
          </Button>,
          <Popconfirm
            key="clear"
            title={t('confirm.clearAllMoments')}
            onConfirm={handleClearAll}
            okText={t('btn.delete')}
            cancelText={t('btn.cancel')}
            okButtonProps={{ danger: true }}
          >
            <Button danger icon={<ClearOutlined />}>
              {t('btn.clearAll')}
            </Button>
          </Popconfirm>,
        ]}
      />
      {preview && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.6)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={() => setPreview(null)}>
          <div style={{ background: '#fff', padding: 16, borderRadius: 8, maxWidth: 600 }} onClick={(e) => e.stopPropagation()}>
            <Image 
              src={preview.url} 
              style={{ maxWidth: '100%' }} 
              fallback="https://via.placeholder.com/600x400?text=图片加载失败"
            />
            <p style={{ marginTop: 12, color: '#666' }}>{preview.caption}</p>
          </div>
        </div>
      )}

      <Modal
        title={t('modal.batchGenerateTitle')}
        open={batchModalOpen}
        onCancel={() => { setBatchModalOpen(false); batchForm.resetFields(); }}
        onOk={() => batchForm.submit()}
        confirmLoading={batchLoading}
        okText={t('btn.generate')}
        cancelText={t('btn.cancel')}
      >
        <Form
          form={batchForm}
          layout="vertical"
          onFinish={handleBatchGenerate}
          initialValues={{ clear_existing: false, moments_per_companion: 3 }}
        >
          <Form.Item
            label={t('batchGenerate.count')}
            name="moments_per_companion"
            rules={[{ required: true }]}
          >
            <InputNumber min={1} max={10} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item label={t('batchGenerate.clearExisting')} name="clear_existing" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Text type="secondary">{t('batchGenerate.hint')}</Text>
        </Form>
      </Modal>
    </div>
  );
}
