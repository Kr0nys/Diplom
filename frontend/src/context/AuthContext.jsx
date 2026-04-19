import { createContext, useContext, useState, useEffect } from 'react';
import { authAPI } from '../api/auth';
import toast from 'react-hot-toast';

const AuthContext = createContext(null);

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const initAuth = async () => {
      if (authAPI.isAuthenticated()) {
        try {
          setUser({ username: 'user' });
        } catch (error) {
          authAPI.logout();
        }
      }
      setLoading(false);
    };

    initAuth();
  }, []);

  const login = async (username, password) => {
    try {
      await authAPI.login(username, password);
      setUser({ username });
      toast.success('Вход выполнен успешно!');
      return true;
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Ошибка входа');
      return false;
    }
  };

  const logout = () => {
    authAPI.logout();
    setUser(null);
    toast.success('Выход выполнен');
  };

  const value = {
    user,
    loading,
    login,
    logout,
    isAuthenticated: authAPI.isAuthenticated()
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return context;
};