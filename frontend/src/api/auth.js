import api from './axios';

function storeTokens(access, refresh) {
  localStorage.setItem('access_token', access);
  localStorage.setItem('refresh_token', refresh);
}

/** Первое сообщение об ошибке из ответа DRF. */
export function formatAuthError(error) {
  const data = error?.response?.data;
  if (!data) return error?.message || 'Ошибка запроса';
  if (typeof data.detail === 'string') return data.detail;
  if (Array.isArray(data.detail)) return data.detail[0] || 'Ошибка запроса';
  for (const key of ['username', 'password', 'password_confirm', 'non_field_errors']) {
    const val = data[key];
    if (Array.isArray(val) && val[0]) return String(val[0]);
    if (typeof val === 'string') return val;
  }
  const firstKey = Object.keys(data)[0];
  const firstVal = data[firstKey];
  if (Array.isArray(firstVal) && firstVal[0]) return String(firstVal[0]);
  if (typeof firstVal === 'string') return firstVal;
  return 'Ошибка запроса';
}

export const authAPI = {
  login: async (username, password) => {
    const response = await api.post('/auth/login/', { username, password });
    storeTokens(response.data.access, response.data.refresh);
    return response.data;
  },

  register: async (username, password, password_confirm) => {
    const response = await api.post('/auth/register/', {
      username,
      password,
      password_confirm,
    });
    storeTokens(response.data.access, response.data.refresh);
    return response.data;
  },

  logout: () => {
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
  },

  me: async () => {
    const response = await api.get('/auth/me/');
    return response.data;
  },

  isAuthenticated: () => {
    return !!localStorage.getItem('access_token');
  },

  getToken: () => {
    return localStorage.getItem('access_token');
  },
};
