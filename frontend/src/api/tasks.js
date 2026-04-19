import api from './axios';

export const tasksAPI = {
  getById: async (id) => {
    const response = await api.get(`/test-tasks/${id}/`);
    return response.data;
  },

  download: async (id) => {
    const response = await api.get(`/test-tasks/${id}/download/`, {
      responseType: 'blob'
    });
    return response.data;
  },

  getAll: async () => {
    const response = await api.get('/test-tasks/');
    return response.data;
  }
};