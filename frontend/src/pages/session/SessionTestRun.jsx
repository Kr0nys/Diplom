import { useMemo, useState, useEffect, useCallback, useRef } from 'react';
import { useOutletContext } from 'react-router-dom';
import { sessionsAPI } from '../../api/sessions';
import { tasksAPI } from '../../api/tasks';
import GenerationConfigSummary from '../../components/tests/GenerationConfigSummary';
import TestViewer from '../../components/tests/TestViewer';
import LoadingSpinner from '../../components/common/LoadingSpinner';
import SessionSectionSkeleton from '../../components/sessions/SessionSectionSkeleton';
import toast from 'react-hot-toast';

function formatStoredLabel(st, index) {
  const date = st.created_at
    ? new Date(st.created_at).toLocaleString('ru-RU', {
        dateStyle: 'short',
        timeStyle: 'short',
      })
    : '—';
  const lines = (st.generated_tests || '').split('\n').length;
  if (st.config?.imported) {
    const fn = st.config.source_filename || 'файл .py';
    return `Импорт · ${fn} · ${date} · ${lines} строк`;
  }
  return `Версия ${index + 1} · ${date} · ${lines} строк`;
}

function StatCard({ label, value, tone = 'neutral' }) {
  const tones = {
    neutral: 'bg-gray-50 border-gray-200 text-gray-900',
    success: 'bg-green-50 border-green-200 text-green-900',
    danger: 'bg-red-50 border-red-200 text-red-900',
    warn: 'bg-amber-50 border-amber-200 text-amber-950',
    muted: 'bg-slate-50 border-slate-200 text-slate-700',
  };
  return (
    <div className={`rounded-lg border px-4 py-3 text-center ${tones[tone] || tones.neutral}`}>
      <div className="text-2xl font-bold tabular-nums">{value ?? '—'}</div>
      <div className="text-xs font-medium uppercase tracking-wide mt-1 opacity-80">{label}</div>
    </div>
  );
}

function TestRunSummary({ summary, passed, exitCode }) {
  const s = summary || {};
  const total = s.total ?? 0;
  const passedCount = s.passed ?? 0;
  const failedCount = s.failed ?? 0;
  const errorCount = s.errors ?? 0;
  const skippedCount = s.skipped ?? 0;
  const duration =
    s.duration_seconds != null ? `${Number(s.duration_seconds).toFixed(2)} с` : null;

  const headline = passed
    ? `Все тесты прошли (${passedCount}${total ? ` из ${total}` : ''})`
    : total > 0
      ? `Упало ${failedCount + errorCount} из ${total} тестов`
      : `pytest завершился с кодом ${exitCode ?? '?'}`;

  return (
    <div className="space-y-4">
      <div
        className={`rounded-lg px-4 py-3 text-sm font-medium ${
          passed
            ? 'bg-green-50 text-green-900 border border-green-200'
            : 'bg-red-50 text-red-900 border border-red-200'
        }`}
      >
        {headline}
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
        <StatCard label="Всего" value={total || '—'} />
        <StatCard label="Прошло" value={passedCount} tone="success" />
        <StatCard label="Упало" value={failedCount} tone={failedCount > 0 ? 'danger' : 'muted'} />
        <StatCard label="Ошибки" value={errorCount} tone={errorCount > 0 ? 'danger' : 'muted'} />
        <StatCard label="Пропущено" value={skippedCount} tone={skippedCount > 0 ? 'warn' : 'muted'} />
      </div>
    </div>
  );
}

function FailureList({ failures }) {
  if (!failures?.length) return null;

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold text-gray-900">
        Упавшие и ошибочные тесты ({failures.length})
      </h3>
      <ul className="space-y-3">
        {failures.map((f, idx) => (
          <li key={`${f.name}-${idx}`} className="rounded-lg border border-red-200 bg-red-50/50 overflow-hidden">
            <div className="px-4 py-3 border-b border-red-100">
              <p className="text-sm font-mono font-medium text-red-950 break-all">{f.name}</p>
              {f.reason && <p className="text-sm text-red-800 mt-1">{f.reason}</p>}
            </div>
            {(f.traceback || f.message) && (
              <pre className="text-xs p-4 overflow-auto max-h-64 bg-white text-gray-800 whitespace-pre-wrap font-mono">
                {f.traceback || f.message}
              </pre>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function PassedTestsList({ cases }) {
  const passed = (cases || []).filter((c) => c.status === 'passed');
  const [open, setOpen] = useState(false);
  if (!passed.length) return null;

  return (
    <div className="space-y-2">
      <button
        type="button"
        className="text-sm font-medium text-primary-700 hover:text-primary-900"
        onClick={() => setOpen((v) => !v)}
      >
        {open ? 'Скрыть' : 'Показать'} прошедшие тесты ({passed.length})
      </button>
      {open && (
        <ul className="rounded-lg border border-green-200 bg-green-50/40 divide-y divide-green-100 text-sm font-mono max-h-48 overflow-auto">
          {passed.map((c) => (
            <li key={c.name} className="px-3 py-2 text-green-900 break-all">
              ✓ {c.name}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function SessionTestRun() {
  const { session, sessionId, loadSession, historyDataPending } = useOutletContext();
  const fileInputRef = useRef(null);
  const [selectedId, setSelectedId] = useState(null);
  const [activeTestCode, setActiveTestCode] = useState('');
  const [running, setRunning] = useState(false);
  const [importing, setImporting] = useState(false);
  const [deletingStoredId, setDeletingStoredId] = useState(null);
  const [result, setResult] = useState(null);
  const [showFullLog, setShowFullLog] = useState(false);

  const storedTests = useMemo(
    () => (Array.isArray(session?.stored_tests) ? session.stored_tests : []),
    [session]
  );
  const storedLimit = session?.stored_tests_limit ?? 5;
  const storedFull = storedTests.length >= storedLimit;

  const selectedStored = useMemo(
    () => (selectedId ? storedTests.find((st) => st.id === selectedId) ?? null : null),
    [storedTests, selectedId]
  );

  useEffect(() => {
    if (selectedStored) {
      setActiveTestCode(selectedStored.generated_tests || '');
    } else {
      setActiveTestCode('');
    }
    setResult(null);
  }, [selectedId, selectedStored?.generated_tests]);

  const toggleVersion = (id) => {
    setSelectedId((prev) => (prev === id ? null : id));
  };

  const hasUploads = Boolean(
    session &&
      ((session.uploads_count ?? 0) > 0 ||
        (Array.isArray(session.uploaded_files) && session.uploaded_files.length > 0))
  );

  const canRun =
    hasUploads &&
    session?.status !== 'PROCESSING' &&
    session?.status !== 'GENERATING_TESTS' &&
    storedTests.length > 0;

  const runPayload = useCallback(
    () => ({
      stored_id: selectedId,
      generated_tests: activeTestCode,
    }),
    [selectedId, activeTestCode]
  );

  const handleImportFile = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;

    if (!file.name.toLowerCase().endsWith('.py')) {
      toast.error('Выберите файл с расширением .py');
      return;
    }

    setImporting(true);
    try {
      const imported = await sessionsAPI.importTestFile(sessionId, file);
      toast.success(`Тест загружен: ${file.name}`);
      await loadSession();
      if (imported?.id) {
        setSelectedId(imported.id);
      }
    } catch (error) {
      const msg = error?.response?.data?.error || error?.message || 'Не удалось загрузить файл';
      toast.error(msg);
    } finally {
      setImporting(false);
    }
  };

  const handleRun = async () => {
    if (!selectedId) {
      toast.error('Выберите или загрузите версию тестов');
      return;
    }
    if (!activeTestCode.trim()) {
      toast.error('Нет кода тестов для запуска');
      return;
    }
    setRunning(true);
    setResult(null);
    try {
      const data = await sessionsAPI.runTests(sessionId, runPayload());
      setResult(data);
      if (data.passed) {
        toast.success(`Все тесты прошли (${data.summary?.passed ?? ''})`);
      } else {
        const failed = (data.summary?.failed ?? 0) + (data.summary?.errors ?? 0);
        toast.error(`Упало тестов: ${failed}`);
      }
    } catch (error) {
      const msg = error?.response?.data?.error || error?.message || 'Не удалось запустить тесты';
      toast.error(msg);
    } finally {
      setRunning(false);
    }
  };

  const handleDeleteStored = async (storedId) => {
    setDeletingStoredId(storedId);
    try {
      await sessionsAPI.deleteStoredTest(sessionId, storedId);
      if (selectedId === storedId) {
        setSelectedId(null);
      }
      toast.success('Версия удалена');
      await loadSession();
    } catch (error) {
      const msg = error?.response?.data?.error || error?.message || 'Не удалось удалить';
      toast.error(msg);
    } finally {
      setDeletingStoredId(null);
    }
  };

  const handleDownloadStored = async () => {
    const st = selectedStored;
    if (!st) return;
    try {
      let blob;
      if (st.source_task_id) {
        blob = await tasksAPI.download(st.source_task_id);
      } else if (activeTestCode.trim()) {
        blob = new Blob([activeTestCode], { type: 'text/x-python;charset=utf-8' });
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
      toast.success('Файл скачан');
    } catch {
      toast.error('Ошибка скачивания');
    }
  };

  if (historyDataPending) {
    return <SessionSectionSkeleton message="Загрузка версий тестов…" />;
  }

  if (!hasUploads) {
    return (
      <div className="card text-gray-600 text-sm">
        Проект не загружен — запуск тестов невозможен.
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="card space-y-4">
        <div className="border-b pb-3">
          <h2 className="text-lg font-semibold">Запуск тестов на проекте</h2>
          <p className="text-sm text-gray-600 mt-1">
            Загрузите ранее сохранённый файл тестов или выберите версию из истории, отредактируйте при
            необходимости и запустите pytest на загруженном проекте.
          </p>
        </div>

        {session.upload_mode === 'FILES' && (
          <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-950">
            Проект загружен отдельными файлами. Сохраняйте относительные пути в именах файлов
            (например, <code className="text-xs">pkg/module.py</code>), иначе импорты между модулями могут не сработать.
          </div>
        )}

        <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 p-4 space-y-3">
          <div>
            <h3 className="text-sm font-semibold text-gray-900">Загрузить тест из файла</h3>
            <p className="text-sm text-gray-600 mt-1">
              Если версию удалили из истории, но файл `.py` остался на компьютере — загрузите его сюда.
              Запись появится в списке ниже и её можно сразу прогнать на проекте.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <input
              ref={fileInputRef}
              type="file"
              accept=".py,text/x-python"
              className="hidden"
              onChange={handleImportFile}
              disabled={importing || storedFull}
            />
            <button
              type="button"
              className="btn-secondary"
              disabled={importing || storedFull || running}
              onClick={() => fileInputRef.current?.click()}
            >
              {importing ? 'Загрузка…' : 'Выбрать файл .py'}
            </button>
            <span className="text-xs text-gray-500">
              Версий в сессии: {storedTests.length} / {storedLimit}
            </span>
          </div>
          {storedFull && (
            <p className="text-sm text-amber-800">
              Лимит версий исчерпан. Удалите запись на этой странице или в «Истории генераций».
            </p>
          )}
        </div>

        {storedTests.length === 0 ? (
          <p className="text-sm text-gray-600">
            Пока нет версий тестов — загрузите файл выше или сгенерируйте тесты на вкладке «Анализ».
          </p>
        ) : (
          <fieldset className="space-y-3">
            <legend className="text-sm font-medium text-gray-800 mb-1">
              Версия для запуска
              <span className="block text-xs font-normal text-gray-500 mt-0.5">
                Повторный клик по выбранной версии снимает выбор и скрывает код
              </span>
            </legend>
            {storedTests.map((st, index) => {
              const id = st.id;
              const checked = selectedId === id;
              return (
                <div
                  key={id}
                  className={`rounded-lg border p-3 transition-colors ${
                    checked
                      ? 'border-primary-300 bg-primary-50/60 ring-1 ring-primary-200'
                      : 'border-gray-200 bg-gray-50'
                  }`}
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div
                      role="button"
                      tabIndex={0}
                      onClick={() => !running && !importing && toggleVersion(id)}
                      onKeyDown={(e) => {
                        if (running || importing) return;
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault();
                          toggleVersion(id);
                        }
                      }}
                      className="flex gap-3 min-w-0 flex-1 cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-primary-400 rounded-md -m-1 p-1"
                    >
                      <input
                        type="radio"
                        name="stored-test-version"
                        value={id}
                        checked={checked}
                        readOnly
                        tabIndex={-1}
                        className="mt-0.5 shrink-0 pointer-events-none"
                        aria-hidden
                      />
                      <span className="text-sm font-medium text-gray-900">
                        {formatStoredLabel(st, index)}
                      </span>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <button
                        type="button"
                        className="btn-secondary text-xs text-red-700 border-red-200"
                        disabled={deletingStoredId === id || running || importing}
                        onClick={() => handleDeleteStored(id)}
                      >
                        {deletingStoredId === id ? 'Удаление…' : 'Удалить'}
                      </button>
                    </div>
                  </div>
                  {st.config?.imported ? (
                    <p className="mt-2 text-xs text-gray-600">
                      Загружено из файла: {st.config.source_filename || '—'}
                    </p>
                  ) : (
                    st.config &&
                    Object.keys(st.config).length > 0 && (
                      <div className="mt-2 rounded-md border border-gray-200 bg-white p-3">
                        <p className="text-xs font-semibold text-gray-600 mb-2">Параметры этой генерации</p>
                        <GenerationConfigSummary config={st.config} />
                      </div>
                    )
                  )}
                </div>
              );
            })}
          </fieldset>
        )}

        <button
          type="button"
          className="btn-primary w-full sm:w-auto"
          onClick={handleRun}
          disabled={!canRun || running || importing || !selectedId}
        >
          {running ? (
            <span className="inline-flex items-center gap-2">
              <LoadingSpinner size="sm" />
              Запуск pytest…
            </span>
          ) : (
            'Запустить тесты'
          )}
        </button>
        <p className="text-xs text-gray-500">
          Запуск использует текущий текст в редакторе (включая несохранённые правки). Сохраните изменения,
          если нужно обновить версию в истории.
        </p>
      </div>

      {selectedStored && (
        <TestViewer
          sessionId={sessionId}
          storedId={selectedStored.id}
          code={selectedStored.generated_tests || ''}
          compact
          onSaved={loadSession}
          onDownload={handleDownloadStored}
          onActiveTextChange={setActiveTestCode}
        />
      )}

      {result && (
        <div className="card space-y-5">
          <TestRunSummary
            summary={result.summary}
            passed={result.passed}
            exitCode={result.exit_code}
          />

          <FailureList failures={result.failures} />

          <PassedTestsList cases={result.cases} />

          <div>
            <button
              type="button"
              className="text-sm font-medium text-gray-700 hover:text-gray-900 mb-2"
              onClick={() => setShowFullLog((v) => !v)}
            >
              {showFullLog ? 'Скрыть' : 'Показать'} полный лог терминала
            </button>
            {showFullLog && (
              <pre className="max-h-[32rem] overflow-auto rounded-lg bg-gray-900 text-gray-100 text-xs p-4 whitespace-pre-wrap font-mono">
                {result.output || '(пустой вывод)'}
              </pre>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
