import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import ProTable, { type ActionType } from '@ant-design/pro-table';
import { Button, Modal, Popconfirm, Space, Typography } from 'antd';
import { DeleteOutlined, EyeOutlined } from '@ant-design/icons';
import { adminFetchJson, adminFetch, showSuccess, showError, formatDate } from '../../api/request';
import type { ChatSessionItem, ChatSessionMessagesResponse } from '../../types';
import { getAppLanguage } from '../../locales';

const { Text, Paragraph } = Typography;

export default function ChatSessionsList() {
  const { t } = useTranslation();
  const actionRef = useRef<ActionType | null>(null);
  const locale = getAppLanguage();

  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detail, setDetail] = useState<ChatSessionMessagesResponse | null>(null);

  const columns = [
    {
      title: t('table.userId'),
      dataIndex: 'user_id',
      key: 'user_id',
      width: 88,
    },
    {
      title: t('table.user'),
      key: 'user',
      render: (_: unknown, record: ChatSessionItem) => (
        <span>
          {record.nickname || record.username || '-'}
          <span style={{ color: '#999', fontSize: 12 }}>
            {' '}
            ({record.username || `#${record.user_id}`})
          </span>
        </span>
      ),
    },
    {
      title: t('table.companionId'),
      dataIndex: 'companion_id',
      key: 'companion_id',
      width: 100,
    },
    {
      title: t('table.companion'),
      dataIndex: 'companion_name',
      key: 'companion_name',
      ellipsis: true,
      render: (_: unknown, record: ChatSessionItem) => record.companion_name || '-',
    },
    {
      title: t('table.messageCount'),
      dataIndex: 'message_count',
      key: 'message_count',
      width: 96,
    },
    {
      title: t('table.turns'),
      dataIndex: 'turns',
      key: 'turns',
      width: 80,
    },
    {
      title: t('table.lastMessageAt'),
      dataIndex: 'last_message_at',
      key: 'last_message_at',
      width: 168,
      render: (_: unknown, record: ChatSessionItem) =>
        formatDate(record.last_message_at, locale),
    },
    {
      title: t('table.sessionUpdatedAt'),
      dataIndex: 'session_updated_at',
      key: 'session_updated_at',
      width: 168,
      render: (_: unknown, record: ChatSessionItem) =>
        formatDate(record.session_updated_at, locale),
    },
    {
      title: t('table.action'),
      key: 'action',
      width: 260,
      render: (_: unknown, record: ChatSessionItem) => (
        <Space size={0} wrap>
          <Button
            type="link"
            size="small"
            icon={<EyeOutlined />}
            onClick={() => openDetail(record.user_id, record.companion_id)}
          >
            {t('btn.viewDetail')}
          </Button>
          <Popconfirm
            title={t('confirm.deleteChatSessionClearTitle')}
            description={t('confirm.deleteChatSessionClearDesc')}
            onConfirm={() => handleDelete(record, true)}
            okText={t('btn.delete')}
            cancelText={t('btn.cancel')}
          >
            <Button type="link" size="small" danger icon={<DeleteOutlined />}>
              {t('btn.deleteAndClear')}
            </Button>
          </Popconfirm>
          <Popconfirm
            title={t('confirm.deleteChatSessionListOnlyTitle')}
            description={t('confirm.deleteChatSessionListOnlyDesc')}
            onConfirm={() => handleDelete(record, false)}
            okText={t('btn.confirmRemove')}
            cancelText={t('btn.cancel')}
          >
            <Button type="link" size="small">
              {t('btn.removeFromListOnly')}
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  async function handleDelete(record: ChatSessionItem, clearMessages: boolean) {
    const q = new URLSearchParams({
      user_id: String(record.user_id),
      companion_id: record.companion_id,
      clear_messages: clearMessages ? 'true' : 'false',
    });
    try {
      const res = await adminFetch(`/api/admin/chat-sessions?${q.toString()}`, {
        method: 'DELETE',
      });
      if (res && res.ok) {
        showSuccess(t('toast.deleteSuccess') as string);
        actionRef.current?.reload();
        if (detail?.user_id === record.user_id && detail?.companion_id === record.companion_id) {
          setDetailOpen(false);
          setDetail(null);
        }
      } else {
        let msg = t('toast.loadFailed') as string;
        if (res) {
          try {
            const err = await res.json();
            msg = err.detail || err.error || msg;
          } catch {
            msg = `${msg} (HTTP ${res.status})`;
          }
        }
        showError(msg);
      }
    } catch (e) {
      showError(e instanceof Error ? e.message : (t('toast.loadFailed') as string));
    }
  }

  async function openDetail(userId: number, companionId: string) {
    setDetailOpen(true);
    setDetailLoading(true);
    setDetail(null);
    try {
      const q = new URLSearchParams({
        user_id: String(userId),
        companion_id: companionId,
        limit: '500',
        offset: '0',
      });
      const res = await adminFetchJson<ChatSessionMessagesResponse>(
        `/api/admin/chat-sessions/messages?${q.toString()}`
      );
      if (res == null) {
        showError((t('toast.authRequired') as string) || '请先登录');
        setDetailOpen(false);
        return;
      }
      setDetail(res);
    } catch (e) {
      showError(e instanceof Error ? e.message : (t('toast.loadFailed') as string));
      setDetailOpen(false);
    } finally {
      setDetailLoading(false);
    }
  }

  return (
    <div>
      <h2>{t('page.chatSessions')}</h2>
      <Paragraph type="secondary" style={{ marginBottom: 16, maxWidth: 720 }}>
        {t('page.chatSessionsHint')}
      </Paragraph>
      <ProTable<ChatSessionItem>
        actionRef={actionRef}
        columns={columns}
        rowKey={(r) => `${r.user_id}-${r.companion_id}`}
        search={false}
        pagination={{ pageSize: 20, showSizeChanger: true }}
        request={async (params) => {
          const page = Math.max(1, Number(params.current) || 1);
          const pageSize = Math.min(100, Math.max(1, Number(params.pageSize) || 20));
          try {
            const q = new URLSearchParams({
              page: String(page),
              page_size: String(pageSize),
            });
            const res = await adminFetchJson<{
              total: number;
              items: ChatSessionItem[];
            }>(`/api/admin/chat-sessions?${q.toString()}`);
            if (res == null) {
              showError((t('toast.authRequired') as string) || '请先登录');
              return { data: [], success: false, total: 0 };
            }
            return {
              data: res.items ?? [],
              success: true,
              total: res.total ?? 0,
            };
          } catch (e) {
            showError(e instanceof Error ? e.message : (t('toast.loadFailed') as string));
            return { data: [], success: false, total: 0 };
          }
        }}
        toolBarRender={() => []}
      />

      <Modal
        title={
          detail
            ? `${t('modal.chatSessionDetail')} — ${detail.nickname || detail.username} × ${detail.companion_name || detail.companion_id}`
            : t('modal.chatSessionDetail')
        }
        open={detailOpen}
        onCancel={() => setDetailOpen(false)}
        footer={null}
        width={720}
        destroyOnClose
      >
        {detailLoading && <Text type="secondary">{t('loading')}</Text>}
        {!detailLoading && detail && (
          <div>
            <Space wrap style={{ marginBottom: 12 }}>
              <Text type="secondary">
                {t('table.userId')}: {detail.user_id}
              </Text>
              <Text type="secondary">
                {t('table.companionId')}: {detail.companion_id}
              </Text>
              <Text type="secondary">
                {t('table.messageCount')}: {detail.messages.length}
                {detail.total > detail.messages.length
                  ? ` / ${detail.total}`
                  : ''}
              </Text>
            </Space>
            <div
              style={{
                maxHeight: '60vh',
                overflowY: 'auto',
                border: '1px solid #f0f0f0',
                borderRadius: 8,
                padding: 12,
                background: '#fafafa',
              }}
            >
              {detail.messages.length === 0 ? (
                <Text type="secondary">{t('empty.messages')}</Text>
              ) : (
                detail.messages.map((m) => (
                  <div
                    key={m.id}
                    style={{
                      marginBottom: 12,
                      padding: '8px 12px',
                      borderRadius: 8,
                      background: m.role === 'user' ? '#e6f7ff' : '#fff',
                      border: '1px solid #e8e8e8',
                    }}
                  >
                    <div style={{ fontSize: 12, color: '#888', marginBottom: 4 }}>
                      {m.role === 'user' ? t('chat.roleUser') : t('chat.roleAssistant')}
                      {m.created_at ? ` · ${formatDate(m.created_at, locale)}` : ''}
                    </div>
                    <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                      {m.content}
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
