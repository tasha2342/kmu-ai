import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import Header from './components/Header.jsx'
import ChatPage from './pages/chat/ChatPage.jsx'
import AuthCallbackPage from './pages/AuthCallbackPage.jsx'
import AdminLayout from './pages/admin/AdminLayout.jsx'
import AdminStatsPage from './pages/admin/AdminStatsPage.jsx'
import AdminUnansweredPage from './pages/admin/AdminUnansweredPage.jsx'
import AdminFaqPage from './pages/admin/AdminFaqPage.jsx'
import AdminLogsPage from './pages/admin/AdminLogsPage.jsx'
import AdminIngestionPage from './pages/admin/AdminIngestionPage.jsx'
import AdminRagPage from './pages/admin/AdminRagPage.jsx'
import AdminMaskingPage from './pages/admin/AdminMaskingPage.jsx'

export default function App() {
  return (
    <BrowserRouter>
      <div className="flex min-h-full flex-col">
        <Header />
        <Routes>
          <Route path="/" element={<ChatPage />} />
          <Route path="/auth/callback" element={<AuthCallbackPage />} />
          <Route path="/admin" element={<AdminLayout />}>
            <Route index element={<AdminStatsPage />} />
            <Route path="unanswered" element={<AdminUnansweredPage />} />
            <Route path="faq" element={<AdminFaqPage />} />
            <Route path="rag" element={<AdminRagPage />} />
            <Route path="masking" element={<AdminMaskingPage />} />
            <Route path="logs" element={<AdminLogsPage />} />
            <Route path="ingestion" element={<AdminIngestionPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
    </BrowserRouter>
  )
}
