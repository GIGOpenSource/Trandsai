import { useEffect, useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ProLayout } from '@ant-design/pro-layout';
import {
  DashboardOutlined,
  UserOutlined,
  CameraOutlined,
  TeamOutlined,
  MessageOutlined,
  NotificationOutlined,
  BookOutlined,
  SettingOutlined,
  LogoutOutlined,
  PictureOutlined,
  BarChartOutlined,
  CommentOutlined,
} from '@ant-design/icons';
import { Button, Space } from 'antd';
import { clearToken, getToken } from '../api/request';
import { setAppLanguage, getAppLanguage } from '../locales';

export default function BasicLayout() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const [lang, setLang] = useState<'zh' | 'en'>(getAppLanguage());

  useEffect(() => {
    if (!getToken()) {
      navigate('/login');
    }
  }, [navigate]);

  const handleLangChange = (l: 'zh' | 'en') => {
    setLang(l);
    setAppLanguage(l);
  };

  const menuItems = [
    { path: '/', key: 'dashboard', icon: <DashboardOutlined />, label: t('nav.dashboard') },
    { path: '/companions', key: 'companions', icon: <UserOutlined />, label: t('nav.companions') },
    { path: '/moments', key: 'moments', icon: <CameraOutlined />, label: t('nav.moments') },
    { path: '/users', key: 'users', icon: <TeamOutlined />, label: t('nav.users') },
    { path: '/chat-sessions', key: 'chat-sessions', icon: <CommentOutlined />, label: t('nav.chatSessions') },
    { path: '/feedback', key: 'feedback', icon: <MessageOutlined />, label: t('nav.feedback') },
    { path: '/notifications', key: 'notifications', icon: <NotificationOutlined />, label: t('nav.notifications') || '系统通知' },
    { path: '/knowledge', key: 'knowledge', icon: <BookOutlined />, label: t('nav.knowledge') },
    { path: '/image-gen', key: 'image-gen', icon: <PictureOutlined />, label: t('nav.imageGen') },
    { path: '/analytics', key: 'analytics', icon: <BarChartOutlined />, label: t('nav.analytics') },
    { path: '/settings', key: 'settings', icon: <SettingOutlined />, label: t('nav.settings') },
  ];

  const selectedKey =
    menuItems.find((item) => location.pathname === item.path || location.pathname.startsWith(item.path + '/'))?.key || 'dashboard';

  return (
    <ProLayout
      title="trandsai"
      logo={false}
      layout="mix"
      fixSiderbar
      fixedHeader
      route={{
        path: '/',
        routes: menuItems.map((item) => ({
          path: item.path,
          name: item.label,
          icon: item.icon,
        })),
      }}
      location={{ pathname: location.pathname }}
      menuItemRender={(item, dom) => (
        <a onClick={() => navigate(item.path || '/')}>{dom}</a>
      )}
      selectedKeys={[selectedKey]}
      actionsRender={() => [
        <Space key="actions">
          <Button
            type={lang === 'zh' ? 'primary' : 'default'}
            size="small"
            onClick={() => handleLangChange('zh')}
          >
            中
          </Button>
          <Button
            type={lang === 'en' ? 'primary' : 'default'}
            size="small"
            onClick={() => handleLangChange('en')}
          >
            En
          </Button>
          <Button
            icon={<LogoutOutlined />}
            size="small"
            danger
            onClick={() => {
              clearToken();
              navigate('/login');
            }}
          >
            {t('logout')}
          </Button>
        </Space>,
      ]}
    >
      <Outlet />
    </ProLayout>
  );
}
