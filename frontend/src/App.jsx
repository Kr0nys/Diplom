import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { Toaster } from 'react-hot-toast';
import { AuthProvider, useAuth } from './context/AuthContext';
import Layout from './components/layout/Layout';
import Login from './pages/Login';
import Register from './pages/Register';
import Dashboard from './pages/Dashboard';
import Upload from './pages/Upload';
import SessionLayout from './pages/session/SessionLayout';
import SessionOverview from './pages/session/SessionOverview';
import SessionRecommendation from './pages/session/SessionRecommendation';
import SessionGenerationHistory from './pages/session/SessionGenerationHistory';
import SessionTestRun from './pages/session/SessionTestRun';
import History from './pages/History';

const PrivateRoute = ({ children }) => {
  const { isAuthenticated, loading } = useAuth();

  if (loading) return null;
  return isAuthenticated ? children : <Navigate to="/login" />;
};

function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route path="/" element={<PrivateRoute><Layout /></PrivateRoute>}>
        <Route index element={<Navigate to="/dashboard" />} />
        <Route path="dashboard" element={<Dashboard />} />
        <Route path="upload" element={<Upload />} />
        <Route path="sessions/:id" element={<SessionLayout />}>
          <Route index element={<SessionOverview />} />
          <Route path="recommendation" element={<SessionRecommendation />} />
          <Route path="generation-history" element={<SessionGenerationHistory />} />
          <Route path="test-run" element={<SessionTestRun />} />
        </Route>
        <Route path="history" element={<History />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Toaster position="top-right" />
        <AppRoutes />
      </AuthProvider>
    </BrowserRouter>
  );
}