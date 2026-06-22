const DETAIL_LEVEL_LABELS = {
  basic: 'Базовый',
  advanced: 'Продвинутый',
  full: 'Полный',
};

function yn(v) {
  if (v === true || v === 'true' || v === 1 || v === '1') return 'да';
  if (v === false || v === 'false' || v === 0 || v === '0') return 'нет';
  return '—';
}

/**
 * Человекочитаемое описание конфигурации генерации (уровень, фреймворк, флаги).
 */
export default function GenerationConfigSummary({ config, className = '' }) {
  if (!config || typeof config !== 'object') {
    return null;
  }

  const detailRaw = String(config.detail_level || config.generation_mode || '').toLowerCase();
  const detailLabel = DETAIL_LEVEL_LABELS[detailRaw] || detailRaw || '—';

  const framework = config.test_framework ? String(config.test_framework) : 'pytest';

  const timeout = config.generation_timeout_sec;

  const rows = [
    { k: 'Уровень детализации', v: detailLabel },
    { k: 'Фреймворк', v: framework },
    { k: 'Моки внешних зависимостей', v: yn(config.use_mocks) },
    { k: 'Граничные случаи', v: yn(config.include_edge_cases) },
    { k: 'LLM-помощник', v: yn(config.llm_assist) },
  ];

  if (framework === 'pytest') {
    rows.push({ k: 'Авто-ремонт по pytest', v: yn(config.pytest_repair) });
  }

  if (timeout != null && timeout !== '') {
    rows.push({ k: 'Таймаут генерации', v: `${timeout} с` });
  }

  return (
    <dl className={`space-y-1.5 text-sm text-gray-700 ${className}`.trim()}>
      {rows.map(({ k, v }) => (
        <div key={k} className="flex flex-wrap gap-x-3 gap-y-0.5 justify-between border-b border-gray-100 pb-1.5 last:border-0 last:pb-0">
          <dt className="text-gray-500 shrink-0">{k}</dt>
          <dd className="text-gray-900 font-medium text-right min-w-0 break-words">{v}</dd>
        </div>
      ))}
    </dl>
  );
}
