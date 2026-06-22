import { useOutletContext } from 'react-router-dom';
import AnalysisResults from '../../components/sessions/AnalysisResults';
import SessionSectionSkeleton from '../../components/sessions/SessionSectionSkeleton';
import TestConfigForm from '../../components/tests/TestConfigForm';
import TestViewer from '../../components/tests/TestViewer';

export default function SessionOverview() {
  const {
    session,
    sessionId,
    generating,
    currentTask,
    showConfig,
    setShowConfig,
    handleGenerateTests,
    handleDownload,
    loadSession,
    storedFull,
    storedLimit,
    metricsPending,
  } = useOutletContext();

  return (
    <div className="space-y-6">
      {metricsPending && (
        <SessionSectionSkeleton message="Ждём результаты анализа…" />
      )}

      {!metricsPending && session.status !== 'PENDING' && session.metrics && (
        <AnalysisResults metrics={session.metrics} report={session.report_text} variant="metrics" />
      )}

      {session.status === 'ANALYZED' && !showConfig && (
        <div className="card text-center py-8">
          <h3 className="text-lg font-semibold mb-4">Анализ завершен!</h3>
          <p className="text-gray-600 mb-6">
            Теперь вы можете сгенерировать юнит-тесты на основе результатов анализа
          </p>
          <button
            type="button"
            onClick={() => setShowConfig(true)}
            className="btn-primary"
            disabled={storedFull}
            title={storedFull ? `Удалите версию в истории (лимит ${storedLimit})` : undefined}
          >
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
          <button
            type="button"
            onClick={() => setShowConfig(true)}
            className="btn-primary"
            disabled={storedFull}
            title={storedFull ? `Удалите версию в истории (лимит ${storedLimit})` : undefined}
          >
            Сгенерировать заново
          </button>
        </div>
      )}

      {showConfig && (
        <div className="card">
          <h3 className="text-lg font-semibold mb-4">Настройки генерации тестов</h3>
          <TestConfigForm onSubmit={handleGenerateTests} loading={generating} submitBlocked={storedFull} />
          <button type="button" onClick={() => setShowConfig(false)} className="btn-secondary mt-4 w-full">
            Отмена
          </button>
        </div>
      )}

      {currentTask?.status === 'FAILED' && currentTask.error_message && (
        <div className="card bg-amber-50 border border-amber-200">
          <h3 className="text-lg font-semibold text-amber-900 mb-2">Генерация не удалась</h3>
          <p className="text-amber-800 text-sm whitespace-pre-wrap">{currentTask.error_message}</p>
        </div>
      )}

      {currentTask && currentTask.generated_tests && !generating && (
        <TestViewer
          sessionId={sessionId}
          code={currentTask.generated_tests}
          onDownload={handleDownload}
          onSaved={loadSession}
        />
      )}

      {session.error_message && (
        <div className="card bg-red-50 border border-red-200">
          <h3 className="text-lg font-semibold text-red-800 mb-2">Ошибка</h3>
          <p className="text-red-700">{session.error_message}</p>
        </div>
      )}
    </div>
  );
}
