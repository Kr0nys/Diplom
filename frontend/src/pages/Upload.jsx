import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { sessionsAPI } from '../api/sessions';
import FileUpload from '../components/common/FileUpload';
import LoadingSpinner from '../components/common/LoadingSpinner';
import toast from 'react-hot-toast';

export default function Upload() {
  const [files, setFiles] = useState([]);
  const [pythonVersion, setPythonVersion] = useState('3.9');
  const [sessionName, setSessionName] = useState('');
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);

  const navigate = useNavigate();

  const handleCreate = async () => {
      if (files.length === 0) {
        toast.error('Выберите хотя бы один файл');
        return;
      }

      setLoading(true);
      try {
        const session = await sessionsAPI.create({
          name: sessionName || `Session ${new Date().toLocaleDateString()}`,
          python_version: pythonVersion,
          dependencies: []
        });

        await sessionsAPI.uploadFiles(session.id, files);

        toast.success('Файлы загружены! Начинается анализ...');
        navigate(`/sessions/${session.id}`);

      } catch (error) {
        console.error('Upload error:', error);
        toast.error('Ошибка загрузки: ' + (error.response?.data?.error || error.message));
      } finally {
        setLoading(false);
      }
  };

  if (uploading) {
    return (
      <div className="max-w-2xl mx-auto">
        <div className="card text-center py-12">
          <LoadingSpinner size="lg" text="Загрузка файлов..." />
          <div className="mt-8">
            <ProgressBar progress={progress} />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Новая сессия</h1>
        <p className="text-gray-600">Загрузите файлы Python проекта для анализа</p>
      </div>

      <div className="card space-y-6">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Название сессии
          </label>
          <input
            type="text"
            value={sessionName}
            onChange={(e) => setSessionName(e.target.value)}
            placeholder="Мой проект"
            className="input-field"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Версия Python
          </label>
          <select
            value={pythonVersion}
            onChange={(e) => setPythonVersion(e.target.value)}
            className="input-field"
          >
            <option value="3.7">Python 3.7</option>
            <option value="3.8">Python 3.8</option>
            <option value="3.9">Python 3.9</option>
            <option value="3.10">Python 3.10</option>
            <option value="3.11">Python 3.11</option>
          </select>
        </div>

        <FileUpload onFilesSelected={setFiles} />

        <button
          onClick={handleCreate}
          disabled={loading || files.length === 0}
          className="btn-primary w-full"
        >
          {loading ? 'Создание...' : 'Загрузить и начать анализ'}
        </button>
      </div>
    </div>
  );
}