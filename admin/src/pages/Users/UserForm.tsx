import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router-dom';
import { Form, Input, Select, Button, Space, Card, Table, InputNumber, Typography, Divider } from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';
import { adminFetchJson, adminFetch, showSuccess, showError, formatDate } from '../../api/request';
import type { UserItem, UserCompanionStatItem, CompanionItem } from '../../types';
import { getAppLanguage } from '../../locales';
import { resolveAdminMediaUrl } from '../../utils/mediaUrl';

export default function UserForm() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { id } = useParams<{ id: string }>();
  const isEdit = !!id;
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const locale = getAppLanguage();

  const [statsLoading, setStatsLoading] = useState(false);
  const [companionStats, setCompanionStats] = useState<UserCompanionStatItem[]>([]);
  const [allCompanions, setAllCompanions] = useState<CompanionItem[]>([]);
  const [affectionDraft, setAffectionDraft] = useState<Record<string, number>>({});
  const [addCompanionId, setAddCompanionId] = useState<string | undefined>();
  const [addAffection, setAddAffection] = useState<number>(0);

  const loadCompanionStats = useCallback(async (opts?: { silent?: boolean }) => {
    if (!isEdit || !id) return;
    setStatsLoading(true);
    try {
      const res = await adminFetchJson<{ items: UserCompanionStatItem[] }>(
        `/api/admin/users/${id}/companion-stats`,
      );
      const items = res?.items ?? [];
      setCompanionStats(items);
      const drafts: Record<string, number> = {};
      items.forEach((row) => {
        drafts[row.companion_id] = row.affection;
      });
      setAffectionDraft(drafts);
    } catch {
      if (!opts?.silent) {
        showError(t('toast.loadFailed') as string);
      }
    } finally {
      setStatsLoading(false);
    }
  }, [id, isEdit, t]);

  const loadCompanionsForSelect = useCallback(async () => {
    if (!isEdit) return;
    try {
      const list = await adminFetchJson<CompanionItem[]>('/api/admin/companions');
      setAllCompanions(Array.isArray(list) ? list : []);
    } catch {
      /* 下拉可选失败时仍可编辑已有行 */
    }
  }, [isEdit]);

  const loadData = useCallback(async () => {
    if (!isEdit || !id) return;
    setLoading(true);
    try {
      const res = await adminFetchJson<UserItem[]>('/api/admin/users');
      const list = Array.isArray(res) ? res : [];
      const item = list.find((u) => String(u.id) === id);
      if (item) {
        form.setFieldsValue({
          username: item.username,
          nickname: item.nickname || '',
          gender: item.gender || '',
          sexual_orientation: item.sexual_orientation || '',
          age: item.age ?? undefined,
          region: item.region || '',
          occupation: item.occupation || '',
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
      void loadCompanionStats();
      void loadCompanionsForSelect();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadData, loadCompanionStats, loadCompanionsForSelect]);

  async function handleSave() {
    const values = await form.validateFields();
    setSaving(true);
    try {
      let res: Response | null;
      if (isEdit) {
        res = await adminFetch(`/api/admin/users/${id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(values),
        });
      } else {
        res = await adminFetch('/api/admin/users', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(values),
        });
      }
      if (res && res.ok) {
        showSuccess(t('toast.saved') as string);
        navigate('/users');
      } else {
        const err = await res?.text();
        showError(err || (t('settings.saveFailed') as string));
      }
    } catch {
      showError(t('settings.saveFailed') as string);
    } finally {
      setSaving(false);
    }
  }

  async function saveAffectionRow(companionId: string) {
    if (!id) return;
    const affection = affectionDraft[companionId];
    if (affection === undefined || Number.isNaN(affection)) {
      showError(t('users.affectionRequired') as string);
      return;
    }
    try {
      const res = await adminFetch(
        `/api/admin/users/${id}/companion-stats/${encodeURIComponent(companionId)}`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ affection }),
        },
      );
      if (res && res.ok) {
        showSuccess(t('toast.saved') as string);
        await loadCompanionStats({ silent: true });
      } else {
        const err = await res?.text();
        showError(err || (t('settings.saveFailed') as string));
      }
    } catch {
      showError(t('settings.saveFailed') as string);
    }
  }

  async function handleAddCompanionStat() {
    if (!id || !addCompanionId) {
      showError(t('users.pickCompanion') as string);
      return;
    }
    try {
      const res = await adminFetch(
        `/api/admin/users/${id}/companion-stats/${encodeURIComponent(addCompanionId)}`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ affection: addAffection }),
        },
      );
      if (res && res.ok) {
        showSuccess(t('toast.saved') as string);
        setAddCompanionId(undefined);
        setAddAffection(0);
        await loadCompanionStats({ silent: true });
      } else {
        const err = await res?.text();
        showError(err || (t('settings.saveFailed') as string));
      }
    } catch {
      showError(t('settings.saveFailed') as string);
    }
  }

  const addOptions = useMemo(() => {
    const existing = new Set(companionStats.map((r) => r.companion_id));
    return allCompanions
      .filter((c) => !existing.has(c.profile.id))
      .map((c) => ({
        value: c.profile.id,
        label: `${c.profile.name} (${c.profile.id})`,
      }));
  }, [allCompanions, companionStats]);

  const statsColumns = [
    {
      title: t('table.avatar'),
      key: 'avatar',
      width: 72,
      render: (_: unknown, row: UserCompanionStatItem) => {
        const src =
          resolveAdminMediaUrl(row.avatar_url) ||
          `https://api.dicebear.com/7.x/avataaars/svg?seed=${row.companion_id}`;
        return (
          <img src={src} alt="" width={40} height={40} style={{ borderRadius: '50%', objectFit: 'cover' }} />
        );
      },
    },
    {
      title: t('users.companionCol'),
      key: 'name',
      render: (_: unknown, row: UserCompanionStatItem) => (
        <div>
          <div>{row.companion_name}</div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {row.companion_id}
          </Typography.Text>
        </div>
      ),
    },
    {
      title: t('table.affection'),
      key: 'affection',
      width: 160,
      render: (_: unknown, row: UserCompanionStatItem) => (
        <InputNumber
          min={0}
          max={100}
          step={0.01}
          style={{ width: '100%' }}
          value={affectionDraft[row.companion_id]}
          onChange={(v) =>
            setAffectionDraft((d) => ({
              ...d,
              [row.companion_id]: typeof v === 'number' ? v : 0,
            }))
          }
        />
      ),
    },
    {
      title: t('users.turnsCol'),
      dataIndex: 'turns',
      key: 'turns',
      width: 88,
    },
    {
      title: t('table.updatedAt'),
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 168,
      render: (iso: string | null) => formatDate(iso, locale),
    },
    {
      title: t('table.action'),
      key: 'action',
      width: 100,
      render: (_: unknown, row: UserCompanionStatItem) => (
        <Button type="link" size="small" onClick={() => void saveAffectionRow(row.companion_id)}>
          {t('btn.save')}
        </Button>
      ),
    },
  ];

  return (
    <div>
      <div style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 12 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/users')}>
          {t('btn.back')}
        </Button>
        <h2 style={{ margin: 0 }}>
          {isEdit ? t('btn.edit') + ' ' + t('page.users') : t('btn.new') + ' ' + t('page.users')}
        </h2>
      </div>

      <Card loading={loading}>
        <Form form={form} layout="vertical">
          <Form.Item
            name="username"
            label={t('table.username')}
            rules={[{ required: !isEdit, message: '用户名至少3个字符' }, { min: 3 }]}
          >
            <Input disabled={isEdit} />
          </Form.Item>
          <Form.Item name="nickname" label={t('table.nickname')}>
            <Input />
          </Form.Item>
          <Form.Item name="age" label={t('table.age')}>
            <InputNumber min={0} max={150} style={{ width: '100%' }} placeholder="-" />
          </Form.Item>
          <Form.Item name="region" label={t('table.region')}>
            <Input allowClear maxLength={120} placeholder="-" />
          </Form.Item>
          <Form.Item name="gender" label={t('table.gender')}>
            <Select allowClear placeholder="-">
              <Select.Option value="男">男</Select.Option>
              <Select.Option value="女">女</Select.Option>
              <Select.Option value="保密">保密</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item name="occupation" label={t('table.occupation')}>
            <Input allowClear maxLength={100} placeholder="-" />
          </Form.Item>
          <Form.Item name="sexual_orientation" label={t('table.sexualOrientation') || '性取向'}>
            <Select allowClear placeholder="-">
              <Select.Option value="heterosexual">异性恋</Select.Option>
              <Select.Option value="homosexual">同性恋</Select.Option>
              <Select.Option value="bisexual">双性恋</Select.Option>
              <Select.Option value="pansexual">泛性恋</Select.Option>
              <Select.Option value="asexual">无性恋</Select.Option>
              <Select.Option value="secret">保密</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item
            name="password"
            label="密码"
            rules={isEdit ? [] : [{ required: true, message: '密码至少6个字符' }, { min: 6 }]}
          >
            <Input.Password placeholder={isEdit ? '留空则不修改' : '请输入密码'} />
          </Form.Item>
          <Space>
            <Button type="primary" onClick={handleSave} loading={saving}>
              {t('btn.save')}
            </Button>
            <Button onClick={() => navigate('/users')}>{t('btn.cancel')}</Button>
          </Space>
        </Form>
      </Card>

      {isEdit && id && (
        <Card
          title={t('users.companionIntimacySection')}
          style={{ marginTop: 16 }}
          loading={statsLoading}
        >
          <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
            {t('users.companionIntimacyHint')}
          </Typography.Paragraph>
          <Table
            rowKey="companion_id"
            size="small"
            pagination={false}
            columns={statsColumns}
            dataSource={companionStats}
            locale={{ emptyText: t('users.noCompanionStats') }}
          />

          <Divider orientation="left" plain>
            {t('users.addCompanionLink')}
          </Divider>
          <Space wrap>
            <Select
              showSearch
              optionFilterProp="label"
              placeholder={t('users.pickCompanion')}
              style={{ minWidth: 260 }}
              value={addCompanionId}
              onChange={(v) => setAddCompanionId(v)}
              options={addOptions}
              allowClear
            />
            <span>{t('table.affection')}</span>
            <InputNumber min={0} max={100} step={0.01} value={addAffection} onChange={(v) => setAddAffection(typeof v === 'number' ? v : 0)} />
            <Button type="default" onClick={() => void handleAddCompanionStat()}>
              {t('users.addLink')}
            </Button>
          </Space>
        </Card>
      )}
    </div>
  );
}
