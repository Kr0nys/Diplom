import { useMemo } from 'react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, PieChart, Pie, Cell } from 'recharts';
import toast from 'react-hot-toast';
import { copyTextToClipboard } from '../../utils/copyToClipboard';

const COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'];

/** @typedef {'full' | 'metrics' | 'recommendations'} AnalysisVariant */

/**
 * @param {object} props
 * @param {object} props.metrics
 * @param {string} [props.report]
 * @param {AnalysisVariant} [props.variant]
 */
export default function AnalysisResults({ metrics, report, variant = 'full' }) {
  const functionData = [
    { name: 'Синхронные', value: (metrics?.functions_count || 0) - (metrics?.async_functions || 0) },
    { name: 'Асинхронные', value: metrics?.async_functions || 0 }
  ];
  const issues = useMemo(() => (Array.isArray(metrics?.issues) ? metrics.issues : []), [metrics]);
  const recommendations = useMemo(
    () => (Array.isArray(metrics?.recommendations) ? metrics.recommendations : []),
    [metrics]
  );

  const displayReport = useMemo(() => {
    if (!report) return '';
    return String(report).replace(/^===\s*ОТЧЁТ АНАЛИЗА КОДА\s*===\s*\n?/u, '');
  }, [report]);

  const ruleTitle = (ruleId) => {
    const id = String(ruleId || '');
    const map = {
      todo_fixme: 'TODO/FIXME осталось в коде',
      print_debug: 'print() в коде',
      print_production_hint: 'print() / консольный вывод и production',
      too_many_args: 'Слишком много параметров',
      mutable_default: 'Изменяемый default-аргумент',
      long_function: 'Слишком длинная функция',
      bare_except: 'bare except',
      except_exception: 'except Exception',
      regex_recompile: 're.compile() при каждом вызове функции',
      loop_string_concat: 'Склейка строк в цикле (+=)',
      redundant_loop_accumulator: 'Цикл вместо простой арифметики',
      case_sensitive_membership: 'Вхождение без нормализации регистра',
      datetime_now_in_loop: 'datetime.now() внутри цикла',
      duplicate_self_method_call: 'Повторный вызов self-метода',
      open_without_with: 'open() без контекстного менеджера',
      sleep_in_code: 'time.sleep() в коде',
    };
    return map[id] || id || 'Potential issue';
  };

  const showMetrics = variant === 'full' || variant === 'metrics';

  const copyAiPrompt = async (it) => {
    const text = [
      'Найди проблему/риск и подскажи, где смотреть (без готового патча).',
      `Rule: ${it?.rule_id || ''}`,
      `Location: ${it?.path || ''}:${it?.line || ''}`,
      `Message: ${it?.message || ''}`,
      it?.excerpt ? `Code: ${it.excerpt}` : '',
    ].filter(Boolean).join('\n');
    try {
      const ok = await copyTextToClipboard(text);
      if (ok) toast.success('Скопировано');
      else toast.error('Не удалось скопировать');
    } catch {
      toast.error('Не удалось скопировать');
    }
  };

  if (variant === 'recommendations') {
    if (recommendations.length === 0 && issues.length === 0) {
      return (
        <div className="card text-gray-600 text-sm">
          По результатам анализа текстовых рекомендаций и замечаний по коду пока нет.
        </div>
      );
    }
    return (
      <div className="space-y-6">
        <div className="card">
          <div className="border-b pb-3 mb-4">
            <h4 className="text-lg font-semibold">Замечания и рекомендации</h4>
            <p className="text-sm text-gray-600 mt-1">
              {issues.length > 0 && <>Замечаний по коду: {issues.length}</>}
              {issues.length > 0 && recommendations.length > 0 && <> · </>}
              {recommendations.length > 0 && <>Рекомендаций по метрикам: {recommendations.length}</>}
            </p>
          </div>

          <div className="max-h-[520px] overflow-auto pr-1 space-y-4">
              {recommendations.length > 0 && (
                <div>
                  <h5 className="text-sm font-semibold text-gray-800 mb-2">Рекомендации по метрикам</h5>
                  <ul className="list-disc pl-5 space-y-1 text-sm text-gray-700">
                    {recommendations.map((r, i) => (
                      <li key={`rec-${i}`}>{r}</li>
                    ))}
                  </ul>
                </div>
              )}
              {issues.length > 0 && (
                <div className="space-y-3">
                  {recommendations.length > 0 && (
                    <h5 className="text-sm font-semibold text-gray-800">Замечания по коду (где смотреть)</h5>
                  )}
                  {issues.slice(0, 200).map((it, idx) => (
                    <div key={`${it?.path || 'file'}:${it?.line || 0}:${idx}`} className="bg-gray-50 rounded-lg p-3 border">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="text-sm font-medium text-gray-900">
                            {ruleTitle(it?.rule_id)}
                          </div>
                          <div className="text-xs text-gray-600 mt-0.5">
                            {it?.path}:{it?.line} • {it?.message}
                          </div>
                        </div>
                        <button
                          type="button"
                          className="btn-secondary text-xs whitespace-nowrap"
                          onClick={() => copyAiPrompt(it)}
                          title="Скопировать краткий запрос для AI-ассистента"
                        >
                          Скопировать для AI
                        </button>
                      </div>
                      {it?.excerpt && (
                        <pre className="mt-2 bg-white p-2 rounded text-xs text-gray-700 whitespace-pre-wrap font-mono border">
                          {it.excerpt}
                        </pre>
                      )}
                    </div>
                  ))}
                  {issues.length > 200 && (
                    <p className="text-xs text-gray-500">Показаны первые 200. Уточним правила — будет меньше “шума”.</p>
                  )}
                </div>
              )}
            </div>
          </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {showMetrics && (
        <>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="card text-center">
          <p className="text-3xl font-bold text-primary-600">{metrics?.files_count || 0}</p>
          <p className="text-sm text-gray-600">Файлов</p>
        </div>
        <div className="card text-center">
          <p className="text-3xl font-bold text-green-600">{metrics?.functions_count || 0}</p>
          <p className="text-sm text-gray-600">Функций</p>
        </div>
        <div className="card text-center">
          <p className="text-3xl font-bold text-purple-600">{metrics?.classes_count || 0}</p>
          <p className="text-sm text-gray-600">Классов</p>
        </div>
        <div className="card text-center">
          <p className="text-3xl font-bold text-orange-600">{metrics?.imports_count || 0}</p>
          <p className="text-sm text-gray-600">Импортов</p>
        </div>
      </div>

      {metrics?.cyclomatic_complexity && (
        <div className="card border-l-4 border-l-blue-400">
          <h4 className="text-lg font-semibold mb-2">Цикломатическая сложность (radon)</h4>
          <p className="text-sm text-gray-600 mb-3">
            Ориентировочная оценка по выборке файлов. Один из индикаторов «насколько разветвлён» код; для
            юнит-тестов удобнее, когда крупные ветвления вынесены в отдельные функции.
          </p>
          <div className="flex flex-wrap gap-4 text-sm">
            <span>
              <span className="text-gray-500">Макс. по выборке:</span>{' '}
              <span className="font-semibold text-gray-900">
                {metrics.cyclomatic_complexity.max ?? '—'}
              </span>
            </span>
            <span>
              <span className="text-gray-500">Среднее по файлам:</span>{' '}
              <span className="font-semibold text-gray-900">
                {metrics.cyclomatic_complexity.avg ?? '—'}
              </span>
            </span>
            <span>
              <span className="text-gray-500">Файлов в выборке:</span>{' '}
              <span className="font-semibold text-gray-900">
                {metrics.cyclomatic_complexity.files_sampled ?? '—'}
              </span>
            </span>
          </div>
          {Array.isArray(metrics.cyclomatic_complexity.hottest_files) &&
            metrics.cyclomatic_complexity.hottest_files.length > 0 && (
              <div className="mt-3">
                <p className="text-xs font-medium text-gray-500 uppercase mb-1">С наибольшей max-сложностью</p>
                <ul className="text-sm text-gray-800 space-y-1">
                  {metrics.cyclomatic_complexity.hottest_files.slice(0, 5).map((h, i) => (
                    <li key={i}>
                      <span className="font-mono text-xs break-all">{h.path}</span>
                      <span className="text-gray-500"> — max {h.max_complexity}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          {metrics.cyclomatic_complexity.hint && (
            <p className="text-xs text-gray-500 mt-3">{metrics.cyclomatic_complexity.hint}</p>
          )}
        </div>
      )}

      <div className="grid md:grid-cols-2 gap-6">
        <div className="card">
          <h4 className="text-lg font-semibold mb-4">Типы функций</h4>
          <ResponsiveContainer width="100%" height={200}>
            <PieChart>
              <Pie data={functionData} cx="50%" cy="50%" outerRadius={60} label>
                {functionData.map((entry, index) => (
                  <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                ))}
              </Pie>
              <Tooltip />
            </PieChart>
          </ResponsiveContainer>
        </div>

        <div className="card">
          <h4 className="text-lg font-semibold mb-4">Структура проекта</h4>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={[
              { name: 'Функции', value: metrics?.functions_count || 0 },
              { name: 'Классы', value: metrics?.classes_count || 0 },
              { name: 'Импорты', value: metrics?.imports_count || 0 }
            ]}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="name" />
              <YAxis />
              <Tooltip />
              <Bar dataKey="value" fill="#3b82f6" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {displayReport && (
        <div className="card">
          <h4 className="text-lg font-semibold mb-4">Отчет анализа</h4>
          <pre className="bg-gray-50 p-4 rounded-lg text-sm text-gray-700 whitespace-pre-wrap font-sans">
            {displayReport}
          </pre>
        </div>
      )}
        </>
      )}

      {variant === 'full' && (recommendations.length > 0 || issues.length > 0) && (
        <div className="card">
          <div className="border-b pb-3 mb-4">
            <h4 className="text-lg font-semibold">Замечания и рекомендации</h4>
            <p className="text-sm text-gray-600 mt-1">
              {issues.length > 0 && <>Замечаний по коду: {issues.length}</>}
              {issues.length > 0 && recommendations.length > 0 && <> · </>}
              {recommendations.length > 0 && <>Рекомендаций по метрикам: {recommendations.length}</>}
            </p>
          </div>

          <div className="max-h-[520px] overflow-auto pr-1 space-y-4">
              {recommendations.length > 0 && (
                <div>
                  <h5 className="text-sm font-semibold text-gray-800 mb-2">Рекомендации по метрикам</h5>
                  <ul className="list-disc pl-5 space-y-1 text-sm text-gray-700">
                    {recommendations.map((r, i) => (
                      <li key={`rec-${i}`}>{r}</li>
                    ))}
                  </ul>
                </div>
              )}
              {issues.length > 0 && (
                <div className="space-y-3">
                  {recommendations.length > 0 && (
                    <h5 className="text-sm font-semibold text-gray-800">Замечания по коду (где смотреть)</h5>
                  )}
                  {issues.slice(0, 200).map((it, idx) => (
                    <div key={`${it?.path || 'file'}:${it?.line || 0}:${idx}`} className="bg-gray-50 rounded-lg p-3 border">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="text-sm font-medium text-gray-900">
                            {ruleTitle(it?.rule_id)}
                          </div>
                          <div className="text-xs text-gray-600 mt-0.5">
                            {it?.path}:{it?.line} • {it?.message}
                          </div>
                        </div>
                        <button
                          type="button"
                          className="btn-secondary text-xs whitespace-nowrap"
                          onClick={() => copyAiPrompt(it)}
                          title="Скопировать краткий запрос для AI-ассистента"
                        >
                          Скопировать для AI
                        </button>
                      </div>
                      {it?.excerpt && (
                        <pre className="mt-2 bg-white p-2 rounded text-xs text-gray-700 whitespace-pre-wrap font-mono border">
                          {it.excerpt}
                        </pre>
                      )}
                    </div>
                  ))}
                  {issues.length > 200 && (
                    <p className="text-xs text-gray-500">Показаны первые 200. Уточним правила — будет меньше “шума”.</p>
                  )}
                </div>
              )}
            </div>
        </div>
      )}
    </div>
  );
}