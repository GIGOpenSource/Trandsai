import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import ProTable, { type ActionType } from '@ant-design/pro-table';
import { Button, Popconfirm, Space, Modal, Form, InputNumber, Select, Image, Progress, Typography, Radio } from 'antd';
import { EditOutlined, DeleteOutlined, PlusOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { adminFetchJson, adminFetch, getToken, showSuccess, showError, formatDate } from '../../api/request';
import type { CompanionItem } from '../../types';
import { getAppLanguage } from '../../locales';
import { resolveAdminMediaUrl } from '../../utils/mediaUrl';

export default function CompanionsList() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const actionRef = useRef<ActionType | null>(null);
  const locale = getAppLanguage();
  const [batchModalVisible, setBatchModalVisible] = useState(false);
  const [batchLoading, setBatchLoading] = useState(false);
  const [batchProgress, setBatchProgress] = useState(0);
  const [batchStatus, setBatchStatus] = useState('');
  const [form] = Form.useForm();
  const batchMode = (Form.useWatch('mode', form) as 'single' | 'all' | undefined) ?? 'single';

  const columns = [
    {
      title: t('table.avatar'),
      key: 'avatar',
      width: 70,
      render: (_: unknown, record: CompanionItem) => (
        record.avatar_generating ? (
          <div style={{ width: 48, height: 48, borderRadius: '50%', background: '#f0f0f0', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <div className="animate-spin" style={{ width: 20, height: 20, border: '2px solid #f0f0f0', borderTopColor: '#ff69b4', borderRadius: '50%' }}></div>
          </div>
        ) : (
          <Image
            src={resolveAdminMediaUrl(record.avatar) || `https://api.dicebear.com/7.x/avataaars/svg?seed=${record.profile.id}`}
            alt={record.profile.name}
            width={48}
            height={48}
            style={{ borderRadius: '50%', objectFit: 'cover' }}
            preview={false}
            fallback="https://api.dicebear.com/7.x/avataaars/svg?seed=fallback"
          />
        )
      ),
    },
    { title: t('table.id'), dataIndex: ['profile', 'id'], key: 'id', width: 80 },
    { title: t('table.name'), dataIndex: ['profile', 'name'], key: 'name' },
    {
      title: t('table.gender'),
      dataIndex: ['profile', 'gender'],
      key: 'gender',
      width: 80,
      render: (_: unknown, record: CompanionItem) => (record.profile.gender === '男' ? '♂' : '♀'),
    },
    { title: t('table.age'), dataIndex: ['profile', 'age'], key: 'age', width: 80 },
    { title: t('table.city'), dataIndex: ['profile', 'city'], key: 'city' },
    {
      title: t('table.affection'),
      dataIndex: ['state', 'affection'],
      key: 'affection',
      width: 100,
    },
    {
      title: t('table.createdAt'),
      dataIndex: ['profile', 'created_at'],
      key: 'created_at',
      render: (_: unknown, record: CompanionItem) => formatDate(record.profile.created_at, locale),
    },
    {
      title: t('table.action'),
      key: 'action',
      width: 180,
      render: (_: unknown, record: CompanionItem) => (
        <Space>
          <Button
            type="link"
            icon={<EditOutlined />}
            onClick={() => navigate(`/companions/${record.profile.id}/edit`)}
          >
            {t('btn.edit')}
          </Button>
          <Popconfirm
            title={t('confirm.deleteCompanion')}
            onConfirm={() => handleDelete(record.profile.id)}
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
      const res = await adminFetchJson<CompanionItem[]>('/api/admin/companions');
      return { data: res || [], success: true, total: (res || []).length };
    } catch {
      showError(t('toast.loadFailed') as string);
      return { data: [], success: false, total: 0 };
    }
  }

  async function handleDelete(id: string) {
    try {
      const res = await adminFetch(`/api/admin/companions/${id}`, { method: 'DELETE' });
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

  async function handleBatchGenerate() {
    const values = await form.validateFields();
    setBatchLoading(true);
    setBatchProgress(0);
    setBatchStatus(t('batchGenerateCompanions.statusPreparing') as string);

    try {
      const isAllLangs = values.mode === 'all';
      const token = getToken();
      const payload: Record<string, unknown> = {};
      if (values.gender) payload.gender = values.gender;
      if (values.sexual_orientation) payload.sexual_orientation = values.sexual_orientation;

      let endpoint = '/api/admin/companions/batch-generate-stream';
      if (isAllLangs) {
        endpoint = '/api/admin/companions/batch-generate-all-langs-stream';
        payload.count_per_lang = values.count;
      } else {
        payload.count = values.count;
        payload.lang = values.lang;
      }

      const savedLang = localStorage.getItem('admin_lang') || 'zh';
      const langMap: Record<string, string> = { zh: 'zh-CN', en: 'en-US' };
      const response = await fetch(endpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
          'Accept-Language': langMap[savedLang] || savedLang,
        },
        body: JSON.stringify(payload),
      });

      if (!response.ok || !response.body) {
        throw new Error('Network response was not ok');
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let created = 0;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed.startsWith('data: ')) continue;
          const jsonStr = trimmed.slice(6);
          if (jsonStr === '[DONE]') continue;

          try {
            const event = JSON.parse(jsonStr);
            if (event.type === 'progress') {
              const percent = isAllLangs
                ? Math.round((event.created_all / event.total_all) * 100)
                : Math.round((event.current / event.total) * 100);
              setBatchProgress(percent);
              if (isAllLangs) {
                setBatchStatus(
                  `${t('lang.' + event.lang)}: ${event.current_lang}/${event.total_lang} | ` +
                  `${t('batchGenerateCompanions.totalProgress')}: ${event.created_all}/${event.total_all}`
                );
                created = event.created_all;
              } else {
                setBatchStatus(
                  t('batchGenerateCompanions.statusProgress', {
                    current: event.current,
                    total: event.total,
                    batch: event.batch,
                    totalBatches: event.total_batches,
                  }) as string
                );
                created = event.current;
              }
            } else if (event.type === 'error') {
              showError(
                t('batchGenerateCompanions.batchError', {
                  batch: event.batch,
                  message: event.message,
                }) as string
              );
            } else if (event.type === 'complete') {
              created = isAllLangs ? event.created_all : event.created;
            }
          } catch {
            // ignore parse error
          }
        }
      }

      showSuccess(t('toast.batchGenerateSuccess', { count: created }) as string);
      setBatchModalVisible(false);
      form.setFieldsValue({ count: 5, lang: 'zh', mode: 'single' });
      actionRef.current?.reload();
    } catch {
      showError(t('toast.batchGenerateFailed') as string);
    } finally {
      setBatchLoading(false);
      setBatchProgress(0);
      setBatchStatus('');
    }
  }

  return (
    <div>
      <h2>{t('page.companions')}</h2>
      <ProTable
        actionRef={actionRef}
        columns={columns}
        rowKey={(record) => record.profile.id}
        request={fetchData}
        search={false}
        polling={(dataSource) => {
          const list = (dataSource as CompanionItem[]) || [];
          return list.some((r) => r.avatar_generating) ? 3000 : 0;
        }}
        pagination={{ pageSize: 10 }}
        toolBarRender={() => [
          <Button
            key="batch"
            icon={<ThunderboltOutlined />}
            onClick={() => {
              form.setFieldsValue({ count: 5, lang: 'zh', mode: 'single' });
              setBatchModalVisible(true);
            }}
          >
            {t('btn.batchGenerateCompanions')}
          </Button>,
          <Button key="new" type="primary" icon={<PlusOutlined />} onClick={() => navigate('/companions/new')}>
            {t('btn.new')}
          </Button>,
        ]}
      />

      <Modal
        title={t('modal.batchGenerateCompanions')}
        open={batchModalVisible}
        onCancel={() => {
          if (!batchLoading) {
            setBatchModalVisible(false);
            setBatchProgress(0);
            setBatchStatus('');
            form.setFieldsValue({ count: 5, lang: 'zh', mode: 'single' });
          }
        }}
        onOk={handleBatchGenerate}
        confirmLoading={batchLoading}
        okText={batchLoading ? t('batchGenerateCompanions.generating') : t('btn.generate')}
        cancelText={t('btn.cancel')}
        closable={!batchLoading}
        maskClosable={!batchLoading}
        keyboard={!batchLoading}
      >
        <Form form={form} layout="vertical" initialValues={{ count: 5, lang: 'zh', mode: 'single' }}>
          {/* 外层无 name，label 不生成 for，避免 for 指到 Radio.Group/Select 的非 labelable 的 div#id（Chrome 校验报错）*/}
          <Form.Item label={t('batchGenerateCompanions.mode')}>
            <Form.Item name="mode" noStyle rules={[{ required: true }]}>
              <Radio.Group disabled={batchLoading}>
                <Radio value="single">{t('batchGenerateCompanions.singleLang')}</Radio>
                <Radio value="all">{t('batchGenerateCompanions.allLangs')}</Radio>
              </Radio.Group>
            </Form.Item>
          </Form.Item>

          <Form.Item
            name="count"
            label={batchMode === 'all' ? t('batchGenerateCompanions.countPerLang') : t('batchGenerateCompanions.count')}
            rules={[{ required: true, type: 'number', min: 1, max: 20 }]}
          >
            <InputNumber min={1} max={20} style={{ width: '100%' }} disabled={batchLoading} />
          </Form.Item>

          {batchMode === 'single' && (
            <Form.Item label={t('batchGenerateCompanions.lang')}>
              <Form.Item name="lang" noStyle rules={[{ required: true }]}>
                <Select disabled={batchLoading}>
                  <Select.Option value="zh">{t('lang.zh')}</Select.Option>
                  <Select.Option value="en">{t('lang.en')}</Select.Option>
                  <Select.Option value="ja">日本語</Select.Option>
                  <Select.Option value="ko">한국어</Select.Option>
                  <Select.Option value="pt">Português</Select.Option>
                  <Select.Option value="es">Español</Select.Option>
                  <Select.Option value="id">Bahasa Indonesia</Select.Option>
                </Select>
              </Form.Item>
            </Form.Item>
          )}

          <Form.Item label={t('batchGenerateCompanions.gender')}>
            <Form.Item name="gender" noStyle>
              <Select allowClear placeholder={t('batchGenerateCompanions.random')} disabled={batchLoading}>
                <Select.Option value="女">{t('gender.female')}</Select.Option>
                <Select.Option value="男">{t('gender.male')}</Select.Option>
              </Select>
            </Form.Item>
          </Form.Item>
          <Form.Item label={t('batchGenerateCompanions.sexualOrientation')}>
            <Form.Item name="sexual_orientation" noStyle>
              <Select allowClear placeholder={t('batchGenerateCompanions.random')} disabled={batchLoading}>
                <Select.Option value="heterosexual">{t('orientation.heterosexual')}</Select.Option>
                <Select.Option value="homosexual">{t('orientation.homosexual')}</Select.Option>
                <Select.Option value="bisexual">{t('orientation.bisexual')}</Select.Option>
                <Select.Option value="pansexual">{t('orientation.pansexual')}</Select.Option>
                <Select.Option value="asexual">{t('orientation.asexual')}</Select.Option>
                <Select.Option value="secret">{t('orientation.secret')}</Select.Option>
              </Select>
            </Form.Item>
          </Form.Item>
          <div style={{ color: '#999', fontSize: 12, marginTop: -8, marginBottom: 16 }}>
            {batchMode === 'all'
              ? t('batchGenerateCompanions.hintAll')
              : t('batchGenerateCompanions.hint')}
          </div>

          {batchLoading && (
            <div style={{ marginTop: 16 }}>
              <Progress percent={batchProgress} status="active" />
              <Typography.Text type="secondary" style={{ display: 'block', marginTop: 8, textAlign: 'center' }}>
                {batchStatus}
              </Typography.Text>
            </div>
          )}
        </Form>
      </Modal>
    </div>
  );
}
