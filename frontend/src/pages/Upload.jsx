import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { sessionsAPI } from '../api/sessions';
import FileUpload from '../components/common/FileUpload';
import LoadingSpinner from '../components/common/LoadingSpinner';
import toast from 'react-hot-toast';

export default function Upload() {
  const [inputMode, setInputMode] = useState('files');
  const [files, setFiles] = useState([]);
  const [githubUrl, setGithubUrl] = useState('');
  const [githubRef, setGithubRef] = useState('');
  const [pythonVersion, setPythonVersion] = useState('3.9');
  const [sessionName, setSessionName] = useState('');
  const [loading, setLoading] = useState(false);

  const navigate = useNavigate();

  const handleFilesSelected = (selected) => {
    const list = selected || [];
    const isArchive = (name = '') => {
      const n = name.toLowerCase();
      return n.endsWith('.zip') || n.endsWith('.tar') || n.endsWith('.tar.gz') || n.endsWith('.tgz');
    };

    const archives = list.filter(f => isArchive(f?.name));
    if (archives.length > 0) {
      if (archives.length > 1) {
        toast.error('Выберите только один архив проекта');
      } else if (list.length > 1) {
        toast('Обнаружен архив. Будет загружен только архив (остальные файлы проигнорированы).');
      }
      setFiles([archives[0]]);
      return;
    }

    setFiles(list);
  };

  const handleCreateFiles = async () => {
    if (files.length === 0) {
      toast.error('Выберите хотя бы один файл');
      return;
    }

    setLoading(true);
    let session = null;
    try {
      session = await sessionsAPI.create({
        name: sessionName || `Session ${new Date().toLocaleDateString()}`,
        python_version: pythonVersion,
        dependencies: [],
      });

      await sessionsAPI.uploadFiles(session.id, files);

      toast.success('Файлы загружены! Начинается анализ...');
      navigate(`/sessions/${session.id}`);
    } catch (error) {
      if (session?.id) {
        try {
          await sessionsAPI.delete(session.id);
        } catch {
          /* ignore cleanup errors */
        }
      }
      console.error('Upload error:', error);
      toast.error('Ошибка загрузки: ' + (error.response?.data?.error || error.message));
    } finally {
      setLoading(false);
    }
  };

  const handleCreateGithub = async () => {
    const url = githubUrl.trim();
    if (!url) {
      toast.error('Укажите ссылку на репозиторий GitHub');
      return;
    }

    setLoading(true);
    try {
      const session = await sessionsAPI.createFromGithub({
        name: sessionName || `Session ${new Date().toLocaleDateString()}`,
        python_version: pythonVersion,
        dependencies: [],
        url,
        ref: githubRef.trim() || undefined,
      });

      toast.success('Репозиторий скачан! Начинается анализ...');
      navigate(`/sessions/${session.id}`);
    } catch (error) {
      console.error('GitHub import error:', error);
      const detail = error.response?.data?.error
        || error.response?.data?.url?.[0]
        || error.message;
      toast.error('Ошибка импорта: ' + detail);
    } finally {
      setLoading(false);
    }
  };

  const canSubmitFiles = files.length > 0;
  const canSubmitGithub = githubUrl.trim().length > 0;

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Новая сессия</h1>
        <p className="text-gray-600">
          Загрузите файлы или архив проекта, либо укажите ссылку на публичный репозиторий GitHub
        </p>
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
            <option value="3.9">Python 3.9</option>
            <option value="3.10">Python 3.10</option>
            <option value="3.11">Python 3.11</option>
            <option value="3.12">Python 3.12</option>
          </select>
        </div>

        <div>
          <span className="block text-sm font-medium text-gray-700 mb-2">Источник проекта</span>
          <div className="flex rounded-lg border border-gray-200 p-1 bg-gray-50">
            <button
              type="button"
              onClick={() => setInputMode('files')}
              className={`flex-1 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                inputMode === 'files'
                  ? 'bg-white text-gray-900 shadow-sm'
                  : 'text-gray-600 hover:text-gray-900'
              }`}
            >
              Файлы / архив
            </button>
            <button
              type="button"
              onClick={() => setInputMode('github')}
              className={`flex-1 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                inputMode === 'github'
                  ? 'bg-white text-gray-900 shadow-sm'
                  : 'text-gray-600 hover:text-gray-900'
              }`}
            >
              GitHub
            </button>
          </div>
        </div>

        {inputMode === 'files' ? (
          <>
            <FileUpload
              onFilesSelected={handleFilesSelected}
              accept=".py,.zip,.tar,.tar.gz,.tgz,.txt"
              multiple={true}
            />

            <button
              onClick={handleCreateFiles}
              disabled={loading || !canSubmitFiles}
              className="btn-primary w-full"
            >
              {loading ? (
                <span className="inline-flex items-center justify-center gap-2">
                  <LoadingSpinner size="sm" />
                  Загрузка...
                </span>
              ) : (
                'Загрузить и начать анализ'
              )}
            </button>
          </>
        ) : (
          <>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Ссылка на репозиторий
              </label>
              <input
                type="url"
                value={githubUrl}
                onChange={(e) => setGithubUrl(e.target.value)}
                placeholder="https://github.com/owner/repo"
                className="input-field"
                disabled={loading}
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Ветка или тег
              </label>
              <input
                type="text"
                value={githubRef}
                onChange={(e) => setGithubRef(e.target.value)}
                placeholder="main"
                className="input-field"
                disabled={loading}
              />
            </div>

            <button
              onClick={handleCreateGithub}
              disabled={loading || !canSubmitGithub}
              className="btn-primary w-full"
            >
              {loading ? (
                <span className="inline-flex items-center justify-center gap-2">
                  <LoadingSpinner size="sm" />
                  Скачивание...
                </span>
              ) : (
                'Скачать с GitHub и начать анализ'
              )}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
