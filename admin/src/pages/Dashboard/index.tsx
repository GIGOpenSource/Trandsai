import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { StatisticCard } from '@ant-design/pro-card';
import { Row, Col, Spin } from 'antd';
import { UserOutlined, MessageOutlined, HeartOutlined, BookOutlined } from '@ant-design/icons';
import { adminFetchJson, showError } from '../../api/request';
import type { StatsData } from '../../types';

export default function Dashboard() {
  const { t } = useTranslation();
  const [data, setData] = useState<StatsData | null>(null);
  const [loading, setLoading] = useState(true);

  const loadStats = useCallback(async () => {
    try {
      const res = await adminFetchJson<StatsData>('/api/admin/stats');
      if (res) setData(res);
    } catch {
      showError(t('toast.loadFailed') as string);
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadStats();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadStats]);

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 60 }}>
        <Spin size="large" />
      </div>
    );
  }

  return (
    <div>
      <h2>{t('page.dashboard')}</h2>
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} sm={12} lg={6}>
          <StatisticCard
            statistic={{
              title: t('stat.companionCount'),
              value: data?.companion_count || 0,
              icon: <UserOutlined style={{ fontSize: 24 }} />,
            }}
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatisticCard
            statistic={{
              title: t('stat.totalTurns'),
              value: data?.total_turns || 0,
              icon: <MessageOutlined style={{ fontSize: 24 }} />,
            }}
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatisticCard
            statistic={{
              title: t('stat.avgAffection'),
              value: data?.avg_affection || 0,
              icon: <HeartOutlined style={{ fontSize: 24 }} />,
            }}
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatisticCard
            statistic={{
              title: t('stat.knowledgeEntries'),
              value: data?.knowledge_stats?.total_entries || 0,
              icon: <BookOutlined style={{ fontSize: 24 }} />,
            }}
          />
        </Col>
      </Row>
    </div>
  );
}
