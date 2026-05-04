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
    const interval = setInterval(loadSession, 5000); // Polling каждые 5 секунд
    return () => clearInterval(interval);
  }, [id]);

  const loadSession = async () => {
    try {
      const data = await sessionsAPI.getById(id);
      setSession(data);

      // Проверяем последнюю задачу генерации тестов
      if (data.latest_test_task) {
        setCurrentTask((prev) => {
          const fromSession = data.latest_test_task;
          // Если API сессии не прислал текст тестов, но он уже есть в текущем state/поле generated_tests,
          // не затираем отображение после смены статуса.
          const generated = fromSession.generated_tests || prev?.generated_tests || data.generated_tests;
          return {
            ...fromSession,
            generated_tests: generated,
          };
        });
      } else if (data.generated_tests) {
        setCurrentTask((prev) => ({
          ...(prev || {}),
          id: prev?.id || 'latest',
          status: prev?.status || 'COMPLETED',
          config: prev?.config || {},
          generated_tests: data.generated_tests,
          error_message: prev?.error_message || '',
        }));
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

      // Начинаем polling задачи
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

      {session.status === 'TESTS_GENERATED' && !showConfig && (
        <div className="card text-center py-8">
          <h3 className="text-lg font-semibold mb-4">Тесты уже сгенерированы</h3>
          <p className="text-gray-600 mb-6">
            Вы можете запустить генерацию повторно с другими настройками.
          </p>
          <button onClick={() => setShowConfig(true)} className="btn-primary">
            Сгенерировать заново
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
      {currentTask && (
        <div className="card bg-blue-50 border border-blue-200">
          <h3 className="text-lg font-semibold text-blue-900 mb-2">Информация о генерации</h3>
          <p className="text-blue-800 text-sm">
            Режим: {currentTask.config?.generation_mode === 'ai' ? 'AI (расширенный)' : 'Базовые правила'}
          </p>
          {currentTask.error_message && (
            <p className="text-amber-700 text-sm mt-2">{currentTask.error_message}</p>
          )}
        </div>
      )}

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