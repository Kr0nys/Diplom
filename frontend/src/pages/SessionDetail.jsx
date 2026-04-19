import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { sessionsAPI } from '../api/sessions';
import { tasksAPI } from '../api/tasks';
import AnalysisResults from '../components/sessions/AnalysisResults';
import TestConfigForm from '../components/tests/TestConfigForm';
import TestViewer from '../components/tests/TestViewer';
import StatusBadge from '../components/common/StatusBadge';
import LoadingSpinner from '../components/common/LoadingSpinner';
import ProgressBar from '../components/common/ProgressBar';
import toast from 'react-hot-toast';

export default function SessionDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [currentTask, setCurrentTask] = useState(null);
  const [showConfig, setShowConfig] = useState(false);

  useEffect(() => {
    loadSession();
    const interval = setInterval(loadSession, 5000);
    return () => clearInterval(interval);
  }, [id]);

  const loadSession = async () => {
    try {
      const data = await sessionsAPI.getById(id);
      setSession(data);

      if (data.status === 'TESTS_GENERATED' && data.test_tasks?.length > 0) {
        const lastTask = data.test_tasks[0];
        setCurrentTask(lastTask);
      }
    } catch (error) {
      toast.error('Ошибка загрузки сессии');
      navigate('/dashboard');
    } finally {
      setLoading(false);
    }
  };

  const handleGenerateTests = async (config) => {
    setGenerating(true);
    try {
      const response = await sessionsAPI.generateTests(id, config);
      toast.success('Генерация тестов началась!');
      setShowConfig(false);

      const checkTask = async () => {
        const task = await tasksAPI.getById(response.task_id);
        setCurrentTask(task);

        if (task.status === 'COMPLETED' || task.status === 'FAILED') {
          setGenerating(false);
          loadSession();
          if (task.status === 'COMPLETED') {
            toast.success('Тесты сгенерированы!');
          }
        } else {
          setTimeout(checkTask, 3000);
        }
      };

      checkTask();
    } catch (error) {
      toast.error('Ошибка генерации тестов');
      setGenerating(false);
    }
  };

  const handleDownload = async () => {
    try {
      const blob = await tasksAPI.download(currentTask.id);
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `tests_${currentTask.id}.py`;
      a.click();
      window.URL.revokeObjectURL(url);
      toast.success('Файл скачан!');
    } catch (error) {
      toast.error('Ошибка скачивания');
    }
  };

  if (loading) {
    return <LoadingSpinner />;
  }

  if (!session) {
    return <div>Сессия не найдена</div>;
  }

  return (
    <div className="space-y-6">
      {/* Заголовок */}
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{session.name}</h1>
          <div className="flex items-center space-x-4 mt-2">
            <StatusBadge status={session.status} />
            <span className="text-sm text-gray-500">Python {session.python_version}</span>
          </div>
        </div>
        <button onClick={() => navigate('/dashboard')} className="btn-secondary">
          ← Назад
        </button>
      </div>

      {/* Прогресс */}
      {session.status === 'PROCESSING' && (
        <div className="card">
          <ProgressBar progress={50} label="Анализ кода..." />
        </div>
      )}

      {/* Результаты анализа */}
      {session.status !== 'PENDING' && session.metrics && (
        <AnalysisResults metrics={session.metrics} report={session.report_text} />
      )}

      {/* Генерация тестов */}
      {session.status === 'ANALYZED' && !showConfig && (
        <div className="card text-center py-8">
          <h3 className="text-lg font-semibold mb-4">Анализ завершен!</h3>
          <p className="text-gray-600 mb-6">
            Теперь вы можете сгенерировать юнит-тесты на основе результатов анализа
          </p>
          <button onClick={() => setShowConfig(true)} className="btn-primary">
            Сгенерировать тесты
          </button>
        </div>
      )}

      {/* Конфигурация генерации */}
      {showConfig && (
        <div className="card">
          <h3 className="text-lg font-semibold mb-4">Настройки генерации тестов</h3>
          <TestConfigForm onSubmit={handleGenerateTests} loading={generating} />
          <button onClick={() => setShowConfig(false)} className="btn-secondary mt-4 w-full">
            Отмена
          </button>
        </div>
      )}

      {/* Результаты генерации */}
      {currentTask && currentTask.generated_tests && (
        <TestViewer code={currentTask.generated_tests} onDownload={handleDownload} />
      )}

      {/* Ошибка */}
      {session.error_message && (
        <div className="card bg-red-50 border border-red-200">
          <h3 className="text-lg font-semibold text-red-800 mb-2">Ошибка</h3>
          <p className="text-red-700">{session.error_message}</p>
        </div>
      )}
    </div>
  );
}