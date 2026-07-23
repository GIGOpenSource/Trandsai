import { lazy, Suspense } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { App as AntdApp, ConfigProvider } from 'antd';
import enUS from 'antd/locale/en_US';
import zhCN from 'antd/locale/zh_CN';
import { useTranslation } from 'react-i18next';
import { getToken } from './api/request';
import BasicLayout from './components/BasicLayout';

const LoginPage = lazy(() => import('./pages/Login'));
const Dashboard = lazy(() => import('./pages/Dashboard'));
const SettingsList = lazy(() => import('./pages/Settings/SettingsList'));
const SettingsForm = lazy(() => import('./pages/Settings/SettingsForm'));
const ImageGen = lazy(() => import('./pages/ImageGen'));
const Analytics = lazy(() => import('./pages/Analytics'));
const CompanionsList = lazy(() => import('./pages/Companions/CompanionsList'));
const CompanionForm = lazy(() => import('./pages/Companions/CompanionForm'));
const MomentsList = lazy(() => import('./pages/Moments/MomentsList'));
const MomentForm = lazy(() => import('./pages/Moments/MomentForm'));
const UsersList = lazy(() => import('./pages/Users/UsersList'));
const UserForm = lazy(() => import('./pages/Users/UserForm'));
const FeedbackList = lazy(() => import('./pages/Feedback/FeedbackList'));
const FeedbackForm = lazy(() => import('./pages/Feedback/FeedbackForm'));
const NotificationsList = lazy(() => import('./pages/Notifications/NotificationsList'));
const NotificationForm = lazy(() => import('./pages/Notifications/NotificationForm'));
const KnowledgeList = lazy(() => import('./pages/Knowledge/KnowledgeList'));
const KnowledgeForm = lazy(() => import('./pages/Knowledge/KnowledgeForm'));
const ChatSessionsList = lazy(() => import('./pages/ChatSessions/ChatSessionsList'));

function RequireAuth({ children }: { children: React.ReactNode }) {
  const token = getToken();
  if (!token) {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
}

function App() {
  const { i18n } = useTranslation();
  const locale = i18n.language === 'en' ? enUS : zhCN;

  return (
    <ConfigProvider locale={locale}>
      <AntdApp>
        <Suspense fallback={null}>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route
              path="/*"
              element={
                <RequireAuth>
                  <BasicLayout />
                </RequireAuth>
              }
            >
              <Route index element={<Dashboard />} />
              <Route path="settings" element={<SettingsList />} />
              <Route path="settings/:id/edit" element={<SettingsForm />} />

              <Route path="companions" element={<CompanionsList />} />
              <Route path="companions/new" element={<CompanionForm />} />
              <Route path="companions/:id/edit" element={<CompanionForm />} />

              <Route path="moments" element={<MomentsList />} />
              <Route path="moments/new" element={<MomentForm />} />
              <Route path="moments/:id/edit" element={<MomentForm />} />

              <Route path="users" element={<UsersList />} />
              <Route path="users/new" element={<UserForm />} />
              <Route path="users/:id/edit" element={<UserForm />} />

              <Route path="feedback" element={<FeedbackList />} />
              <Route path="feedback/new" element={<FeedbackForm />} />
              <Route path="feedback/:id/edit" element={<FeedbackForm />} />

              <Route path="chat-sessions" element={<ChatSessionsList />} />

              <Route path="notifications" element={<NotificationsList />} />
              <Route path="notifications/new" element={<NotificationForm />} />

              <Route path="knowledge" element={<KnowledgeList />} />
              <Route path="knowledge/new" element={<KnowledgeForm />} />
              <Route path="knowledge/:id/edit" element={<KnowledgeForm />} />

              <Route path="image-gen" element={<ImageGen />} />
              <Route path="analytics" element={<Analytics />} />
            </Route>
          </Routes>
        </Suspense>
      </AntdApp>
    </ConfigProvider>
  );
}

export default App;
