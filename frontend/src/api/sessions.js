import api from './axios';

export const sessionsAPI = {
  getAll: async (params = {}) => {
    const response = await api.get('/sessions/', { params });
    return response.data;
  },
  list: async (params = {}) => sessionsAPI.getAll(params),

  get: async (id) => {
    const response = await api.get(`/sessions/${id}/`);
    return response.data;
  },
  getById: async (id) => sessionsAPI.get(id),
  load: async (id) => sessionsAPI.get(id),
  fetch: async (id) => sessionsAPI.get(id),

  create: async (data) => {
    const response = await api.post('/sessions/', data);
    return response.data;
  },

  uploadFiles: async (sessionId, files) => {
    const formData = new FormData();
    const list = files || [];
    const isArchive = (name = '') => {
      const n = String(name || '').toLowerCase();
      return n.endsWith('.zip') || n.endsWith('.tar') || n.endsWith('.tar.gz') || n.endsWith('.tgz');
    };

    const archive = list.find(f => isArchive(f?.name));
    if (archive) {
      formData.append('archive', archive);
    } else {
      list.forEach(file => formData.append('files', file));
    }
    const response = await api.post(`/sessions/${sessionId}/upload_files/`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' }
    });
    return response.data;
  },

  createFromGithub: async (data) => {
    const response = await api.post('/sessions/from-github/', data);
    return response.data;
  },

  importGithub: async (sessionId, { url, ref } = {}) => {
    const body = { url };
    if (ref) body.ref = ref;
    const response = await api.post(`/sessions/${sessionId}/import-github/`, body);
    return response.data;
  },

  /** Повторный анализ по файлам, уже привязанным к сессии */
  reanalyze: async (sessionId) => {
    const response = await api.post(`/sessions/${sessionId}/reanalyze/`);
    return response.data;
  },

  getProjectTree: async (sessionId) => {
    const response = await api.get(`/sessions/${sessionId}/project-tree/`);
    return response.data;
  },

  generateTests: async (sessionId, config) => {
    const response = await api.post(`/sessions/${sessionId}/generate_tests/`, config);
    return response.data;
  },

  deleteStoredTest: async (sessionId, storedId) => {
    await api.delete(`/sessions/${sessionId}/stored_tests/${storedId}/`);
  },
  getTests: async (sessionId) => {
    const response = await api.get(`/sessions/${sessionId}/tests/`);
    return response.data;
  },
  runTests: async (sessionId, body = {}) => {
    const response = await api.post(`/sessions/${sessionId}/tests/run/`, body);
    return response.data;
  },

  importTestFile: async (sessionId, file) => {
    const formData = new FormData();
    formData.append('file', file);
    const response = await api.post(`/sessions/${sessionId}/tests/import/`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return response.data;
  },

  /** Тело `{ generated_tests }` опционально: если есть — проверяется этот текст (черновик), иначе сохранённые тесты сессии */
  validateTests: async (sessionId, body = {}) => {
    const response = await api.post(`/sessions/${sessionId}/tests/validate/`, body);
    return response.data;
  },
  /** Ручное сохранение отредактированных тестов (PATCH). Опционально stored_id для записи из истории. */
  patchGeneratedTests: async (sessionId, payload) => {
    const response = await api.patch(`/sessions/${sessionId}/tests/`, payload);
    return response.data;
  },
  downloadTests: (sessionId) => {
    window.open(`/api/sessions/${sessionId}/tests/download/`, '_blank');
  },

  getStatus: async (sessionId) => {
    const response = await api.get(`/sessions/${sessionId}/status/`);
    return response.data;
  },

  delete: async (id) => {
    const response = await api.delete(`/sessions/${id}/`);
    return response.data;
  },
};