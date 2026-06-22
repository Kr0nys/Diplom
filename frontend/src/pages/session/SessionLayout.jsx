import { useState, useEffect, useMemo, useCallback } from 'react';
import { useParams, useNavigate, Outlet } from 'react-router-dom';
import { sessionsAPI } from '../../api/sessions';
import { tasksAPI } from '../../api/tasks';
import SessionAnalysisNav from '../../components/sessions/SessionAnalysisNav';
import { useProjectTree } from '../../hooks/useProjectTree';
import StatusBadge from '../../components/common/StatusBadge';
import LoadingSpinner from '../../components/common/LoadingSpinner';
import ProgressBar from '../../components/common/ProgressBar';
import toast from 'react-hot-toast';

export default function SessionLayout() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [reanalyzeLoading, setReanalyzeLoading] = useState(false);
  const [currentTask, setCurrentTask] = useState(null);
  const [showConfig, setShowConfig] = useState(false);

  const storedTests = useMemo(
    () => (Array.isArray(session?.stored_tests) ? session.stored_tests : []),
    [session]
  );
  const storedLimit = session?.stored_tests_limit ?? 5;
  const storedFull = storedTests.length >= storedLimit;

  const metricsPending = Boolean(
    session && !session.metrics && (session.status === 'PENDING' || session.status === 'PROCESSING')
  );
  const historyDataPending = Boolean(session && session.stored_tests == null);

  const hasUploads = Boolean(
    session &&
      ((session.uploads_count ?? 0) > 0 ||
        (Array.isArray(session.uploaded_files) && session.uploaded_files.length > 0))
  );

  const { projectTree, treeLoading } = useProjectTree(id, session, hasUploads);

  const sidebarProps = {
    projectTree,
    treeLoading,
    showProjectTree: hasUploads,
  };

  const loadSession = useCallback(async () => {
    try {
      const data = await sessionsAPI.getById(id);
      setSession(data);

      if (data.latest_test_task) {
        setCurrentTask(() => {
          const fromSession = data.latest_test_task;
          const hideBody =
            fromSession.status === 'GENERATING' ||
            fromSession.status === 'PENDING' ||
            data.status === 'GENERATING_TESTS';
          const generated = hideBody
            ? ''
            : (fromSession.generated_tests || data.generated_tests || '');
          return {
            ...fromSession,
            generated_tests: generated,
          };
        });
      } else if (data.generated_tests && data.status !== 'GENERATING_TESTS') {
        setCurrentTask({
          id: 'latest',
          status: 'COMPLETED',
          config: {},
          generated_tests: data.generated_tests,
          error_message: '',
        });
      }
    } catch (error) {
      toast.error('Ошибка загрузки сессии');
      navigate('/dashboard');
    } finally {
      setLoading(false);
    }
  }, [id, navigate]);

  useEffect(() => {
    setLoading(true);
    loadSession();
    const interval = setInterval(loadSession, 5000);
    return () => clearInterval(interval);
  }, [id, loadSession]);

  const handleReanalyze = async () => {
    if (!(session?.uploads_count > 0) && !(Array.isArray(session?.uploaded_files) && session.uploaded_files.length > 0))
      return;
    setReanalyzeLoading(true);
    try {
      await sessionsAPI.reanalyze(id);
      toast.success('Повторный анализ запущен');
      await loadSession();
    } catch (error) {
      const msg = error?.response?.data?.error || error?.message || 'Не удалось запустить анализ';
      toast.error(msg);
    } finally {
      setReanalyzeLoading(false);
    }
  };

  const handleGenerateTests = useCallback(
    async (config) => {
      setGenerating(true);
      try {
        const response = await sessionsAPI.generateTests(id, config);
        toast.success('Генерация тестов началась!');
        setShowConfig(false);
        setCurrentTask({
          id: response.task_id,
          status: 'GENERATING',
          generated_tests: '',
          error_message: '',
          config: {},
        });

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
        const msg = error?.response?.data?.error || error?.message || 'Ошибка генерации тестов';
        toast.error(msg);
        setGenerating(false);
      }
    },
    [id, loadSession]
  );

  const handleDownload = useCallback(async () => {
    if (!currentTask?.id) return;
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
  }, [currentTask?.id]);

  const outletContext = useMemo(
    () => ({
      sessionId: id,
      session,
      loadSession,
      generating,
      setGenerating,
      currentTask,
      setCurrentTask,
      showConfig,
      setShowConfig,
      handleGenerateTests,
      handleDownload,
      storedFull,
      storedLimit,
      metricsPending,
      historyDataPending,
    }),
    [
      id,
      session,
      loadSession,
      generating,
      currentTask,
      showConfig,
      handleGenerateTests,
      handleDownload,
      storedFull,
      storedLimit,
      metricsPending,
      historyDataPending,
    ]
  );

  if (loading) {
    return <LoadingSpinner />;
  }

  if (!session) {
    return <div>Сессия не найдена</div>;
  }

  return (
    <div className="flex flex-col xl:flex-row xl:gap-8 xl:items-start">
      <div className="flex-1 min-w-0 space-y-6 xl:order-1">
        <div className="flex justify-between items-center gap-3 flex-wrap">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">{session.name}</h1>
            <div className="flex items-center space-x-4 mt-2 flex-wrap gap-y-1">
              <StatusBadge status={session.status} />
              <span className="text-sm text-gray-500">Python {session.python_version}</span>
              {session.upload_mode === 'GITHUB' && session.source_url && (
                <a
                  href={session.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-sm text-blue-600 hover:underline truncate max-w-md"
                  title={session.source_url}
                >
                  GitHub
                </a>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0 flex-wrap justify-end">
            {hasUploads &&
              session.status !== 'PROCESSING' &&
              session.status !== 'GENERATING_TESTS' && (
                <button
                  type="button"
                  onClick={handleReanalyze}
                  disabled={reanalyzeLoading}
                  className="btn-secondary"
                  title="Запустить анализ снова по уже загруженным файлам"
                >
                  {reanalyzeLoading ? 'Запуск…' : 'Повторить анализ'}
                </button>
              )}
            <button type="button" onClick={() => navigate('/dashboard')} className="btn-secondary">
              ← Назад
            </button>
          </div>
        </div>

        <SessionAnalysisNav variant="mobile" sessionId={id} session={session} {...sidebarProps} />

        {session.status === 'PROCESSING' && (
          <div className="card">
            <ProgressBar progress={50} label="Анализ кода..." />
          </div>
        )}

        {session.status === 'GENERATING_TESTS' && (
          <div className="card">
            <ProgressBar progress={65} label="Генерация тестов..." />
          </div>
        )}

        {storedFull && (
          <div className="card border border-amber-200 bg-amber-50 text-sm text-amber-950">
            Сохранено максимум версий тестов ({storedLimit}). Удалите одну или несколько записей на вкладке «История
            генераций», затем снова запустите генерацию.
          </div>
        )}

        <Outlet context={outletContext} />
      </div>

      <SessionAnalysisNav variant="desktop" sessionId={id} session={session} {...sidebarProps} />
    </div>
  );
}
