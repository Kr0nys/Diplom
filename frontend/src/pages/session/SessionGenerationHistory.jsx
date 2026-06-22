import { useMemo, useState } from 'react';
import { useOutletContext } from 'react-router-dom';
import { sessionsAPI } from '../../api/sessions';
import { tasksAPI } from '../../api/tasks';
import GenerationConfigSummary from '../../components/tests/GenerationConfigSummary';
import TestViewer from '../../components/tests/TestViewer';
import SessionSectionSkeleton from '../../components/sessions/SessionSectionSkeleton';
import toast from 'react-hot-toast';

const HISTORY_LIST_VISIBLE = 3;

export default function SessionGenerationHistory() {
  const { session, sessionId, loadSession, historyDataPending } = useOutletContext();
  const [expandedStoredId, setExpandedStoredId] = useState(null);
  const [deletingStoredId, setDeletingStoredId] = useState(null);
  const [historyListExpanded, setHistoryListExpanded] = useState(false);

  const storedTests = useMemo(
    () => (Array.isArray(session?.stored_tests) ? session.stored_tests : []),
    [session]
  );
  const storedLimit = session?.stored_tests_limit ?? 5;
  const visibleStoredTests = historyListExpanded ? storedTests : storedTests.slice(0, HISTORY_LIST_VISIBLE);
  const hasMoreStoredThanVisible = storedTests.length > HISTORY_LIST_VISIBLE;

  const handleDeleteStored = async (storedId) => {
    setDeletingStoredId(storedId);
    try {
      await sessionsAPI.deleteStoredTest(sessionId, storedId);
      if (expandedStoredId === storedId) setExpandedStoredId(null);
      toast.success('Версия удалена');
      await loadSession();
    } catch (error) {
      const msg = error?.response?.data?.error || error?.message || 'Не удалось удалить';
      toast.error(msg);
    } finally {
      setDeletingStoredId(null);
    }
  };

  /** Как на основной вкладке: через задачу API или текст версии из истории */
  const handleDownloadStored = async (st) => {
    const raw = st.generated_tests ?? '';
    try {
      let blob;
      if (st.source_task_id) {
        blob = await tasksAPI.download(st.source_task_id);
      } else if (String(raw).trim()) {
        blob = new Blob([raw], { type: 'text/x-python;charset=utf-8' });
      } else {
        toast.error('Нет текста для скачивания');
        return;
      }
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `tests_${st.source_task_id || st.id}.py`;
      a.click();
      window.URL.revokeObjectURL(url);
      toast.success('Файл скачан!');
    } catch {
      toast.error('Ошибка скачивания');
    }
  };

  if (historyDataPending) {
    return <SessionSectionSkeleton message="Загрузка истории генераций…" />;
  }

  if (storedTests.length === 0) {
    return (
      <div className="card text-gray-600 text-sm">
        История сохранённых генераций пока пуста. После генерации тестов версии появятся здесь.
      </div>
    );
  }

  return (
    <div className="card space-y-4">
      <div className="border-b pb-3">
        <h2 className="text-lg font-semibold">История генераций</h2>
        <p className="text-sm text-gray-600 mt-1">
          Записей: {storedTests.length} из {storedLimit}
          {hasMoreStoredThanVisible && (
            <>
              {' '}
              · ниже по умолчанию {HISTORY_LIST_VISIBLE} последних
            </>
          )}
        </p>
      </div>

      <div className="space-y-4">
        <p className="text-sm text-gray-600">
          {hasMoreStoredThanVisible && !historyListExpanded
            ? `Показаны последние ${HISTORY_LIST_VISIBLE} записи — кнопкой ниже можно открыть весь список.`
            : 'Параметры каждой генерации, код и удаление версий.'}
        </p>
        <ul className="space-y-3">
          {visibleStoredTests.map((st) => {
            const raw = st.generated_tests || '';
            const lines = raw.split('\n');

            return (
              <li key={st.id} className="border rounded-lg p-3 bg-gray-50">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="text-sm text-gray-800">
                    <span className="font-medium">
                      {st.created_at
                        ? new Date(st.created_at).toLocaleString('ru-RU', {
                            dateStyle: 'short',
                            timeStyle: 'short',
                          })
                        : '—'}
                    </span>
                    <span className="text-gray-400 ml-2">· {lines.length} строк</span>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      className="btn-secondary text-xs"
                      onClick={() => setExpandedStoredId((cur) => (cur === st.id ? null : st.id))}
                    >
                      {expandedStoredId === st.id ? 'Свернуть' : 'Показать код'}
                    </button>
                    <button
                      type="button"
                      className="btn-secondary text-xs text-red-700 border-red-200"
                      disabled={deletingStoredId === st.id}
                      onClick={() => handleDeleteStored(st.id)}
                    >
                      {deletingStoredId === st.id ? 'Удаление…' : 'Удалить'}
                    </button>
                  </div>
                </div>
                {st.config && Object.keys(st.config).length > 0 && (
                  <div className="mt-2 rounded-md border border-gray-200 bg-white p-3">
                    <p className="text-xs font-semibold text-gray-600 mb-2">Параметры этой генерации</p>
                    <GenerationConfigSummary config={st.config} />
                  </div>
                )}
                {expandedStoredId === st.id && raw && (
                  <div className="mt-2">
                    <TestViewer
                      sessionId={sessionId}
                      storedId={st.id}
                      code={raw}
                      compact
                      onSaved={loadSession}
                      onDownload={() => handleDownloadStored(st)}
                    />
                  </div>
                )}
              </li>
            );
          })}
        </ul>
        {hasMoreStoredThanVisible && (
          <div>
            <button
              type="button"
              className="btn-secondary text-sm"
              onClick={() => {
                setHistoryListExpanded((v) => {
                  if (v) {
                    setExpandedStoredId(null);
                  }
                  return !v;
                });
              }}
            >
              {historyListExpanded ? 'Свернуть список' : `Показать все ${storedTests.length} записей`}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
