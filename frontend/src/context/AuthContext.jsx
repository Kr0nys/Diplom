import { createContext, useContext, useState, useEffect } from 'react';
import { authAPI, formatAuthError } from '../api/auth';
import toast from 'react-hot-toast';

const AuthContext = createContext(null);

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const initAuth = async () => {
      if (authAPI.isAuthenticated()) {
        try {
          const data = await authAPI.me();
          setUser({ id: data.id, username: data.username });
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
      const data = await authAPI.me();
      setUser({ id: data.id, username: data.username });
      toast.success('Вход выполнен успешно!');
      return true;
    } catch (error) {
      toast.error(formatAuthError(error));
      return false;
    }
  };

  const register = async (username, password, passwordConfirm) => {
    try {
      const data = await authAPI.register(username, password, passwordConfirm);
      setUser({ id: data.id, username: data.username });
      toast.success('Регистрация успешна!');
      return true;
    } catch (error) {
      toast.error(formatAuthError(error));
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
    register,
    logout,
    isAuthenticated: authAPI.isAuthenticated(),
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
