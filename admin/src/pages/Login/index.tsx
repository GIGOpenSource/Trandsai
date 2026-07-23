import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Card, Input, Button, Form, Typography, Space, message } from 'antd';
import { LockOutlined } from '@ant-design/icons';
import { setToken } from '../../api/request';
import { useNavigate } from 'react-router-dom';

const { Title } = Typography;

export default function LoginPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);

  const handleLogin = async (values: { password: string }) => {
    if (!values.password) {
      message.warning(t('login.error.empty'));
      return;
    }
    setLoading(true);
    try {
      const res = await fetch('/api/admin/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: values.password }),
      });
      const data = await res.json();
      if (res.ok && data.token) {
        setToken(data.token);
        message.success(t('toast.saved'));
        navigate('/');
      } else {
        message.error(t('login.error.wrong'));
      }
    } catch {
      message.error(t('login.error.failed'));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: '#f0f2f5',
      }}
    >
      <Card style={{ width: 420, boxShadow: '0 4px 12px rgba(0,0,0,0.1)' }}>
        <Space direction="vertical" style={{ width: '100%' }} align="center">
          <Title level={3}>{t('login.title')}</Title>
        </Space>
        <Form onFinish={handleLogin} style={{ marginTop: 24 }}>
          <Form.Item
            name="password"
            rules={[{ required: true, message: t('login.error.empty') as string }]}
          >
            <Input.Password
              prefix={<LockOutlined />}
              placeholder={t('login.placeholder') as string}
              size="large"
              onPressEnter={() => {}}
            />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" block size="large" loading={loading}>
              {loading ? t('login.loggingIn') : t('btn.login')}
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}
