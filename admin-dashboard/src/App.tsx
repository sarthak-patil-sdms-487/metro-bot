import { BrowserRouter, Navigate, Routes, Route } from 'react-router-dom';
import { AuthProvider } from './context/AuthContext';
import ProtectedRoute from './components/ProtectedRoute';
import LoginPage from './pages/LoginPage';
import PublicDashboard from './pages/PublicDashboard';
import DashboardLayout from './components/layout/DashboardLayout';
import DashboardPage from './pages/DashboardPage';
import UsersPage from './pages/UsersPage';
import ConversationsPage from './pages/ConversationsPage';
import TicketsPage from './pages/TicketsPage';
import LogsPage from './pages/LogsPage';
import CostAuditPage from './pages/CostAuditPage';
import { ThemeProvider } from './context/ThemeContext';
import { SidebarProvider } from './context/SidebarContext';

function App() {
  return (
    <ThemeProvider defaultTheme="dark" storageKey="vite-ui-theme">
      <BrowserRouter>
        <AuthProvider>
          <SidebarProvider>
            <Routes>
              <Route path="/" element={<PublicDashboard />} />
              <Route path="/login" element={<LoginPage />} />
              <Route 
                path="/admin" 
                element={
                  <ProtectedRoute>
                    <DashboardLayout />
                  </ProtectedRoute>
                }
              >
                <Route index element={<DashboardPage />} />
                <Route path="users" element={<UsersPage />} />
                <Route path="conversations" element={<ConversationsPage />} />
                <Route path="tickets" element={<TicketsPage />} />
                <Route path="calls" element={<Navigate to="/admin/conversations" replace />} />
                <Route path="logs" element={<LogsPage />} />
                <Route path="cost-audit" element={<CostAuditPage />} />
              </Route>
            </Routes>
          </SidebarProvider>
        </AuthProvider>
      </BrowserRouter>
    </ThemeProvider>
  );
}

export default App;
