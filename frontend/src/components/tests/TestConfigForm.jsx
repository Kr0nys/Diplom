import { useState } from 'react';

export default function TestConfigForm({ onSubmit, loading, submitBlocked }) {
  const [config, setConfig] = useState({
    detail_level: 'advanced',
    use_mocks: true,
    include_edge_cases: true,
    test_framework: 'pytest',
    pytest_repair: true,
    llm_assist: false,
  });

  const handleSubmit = (e) => {
    e.preventDefault();
    onSubmit(config);
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">
          Уровень детализации
        </label>
        <select
          value={config.detail_level}
          onChange={(e) => setConfig({ ...config, detail_level: e.target.value })}
          className="input-field"
        >
          <option value="basic">Базовый (детерминированно, быстро)</option>
          <option value="advanced">Продвинутый (AST/рецепты, без LLM)</option>
          <option value="full">Полный (basic + AST/рецепты, без LLM)</option>
        </select>
      </div>

      <div className="space-y-3">
        <label className="flex items-center space-x-3">
          <input
            type="checkbox"
            checked={config.use_mocks}
            onChange={(e) => setConfig({ ...config, use_mocks: e.target.checked })}
            className="w-4 h-4 text-primary-600 rounded"
          />
          <span className="text-sm text-gray-700">
            Использовать моки для внешних зависимостей
            <span className="block text-xs text-gray-500">Только при включённом LLM-помощнике</span>
          </span>
        </label>

        <label className="flex items-center space-x-3">
          <input
            type="checkbox"
            checked={config.include_edge_cases}
            onChange={(e) => setConfig({ ...config, include_edge_cases: e.target.checked })}
            className="w-4 h-4 text-primary-600 rounded"
          />
          <span className="text-sm text-gray-700">Включить граничные случаи</span>
        </label>

        <label className="flex items-center space-x-3">
          <input
            type="checkbox"
            checked={config.llm_assist}
            onChange={(e) => setConfig({ ...config, llm_assist: e.target.checked })}
            className="w-4 h-4 text-primary-600 rounded"
          />
          <span className="text-sm text-gray-700">LLM-помощник (опционально добавляет/улучшает тесты)</span>
        </label>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">
          Тестовый фреймворк
        </label>
        <select
          value={config.test_framework}
          onChange={(e) => setConfig({ ...config, test_framework: e.target.value })}
          className="input-field"
        >
          <option value="pytest">pytest</option>
          <option value="unittest">unittest</option>
        </select>
      </div>

      <div className="space-y-3">
        <label className="flex items-center space-x-3">
          <input
            type="checkbox"
            checked={config.pytest_repair}
            onChange={(e) => setConfig({ ...config, pytest_repair: e.target.checked })}
            className="w-4 h-4 text-primary-600 rounded"
            disabled={config.test_framework !== 'pytest'}
          />
          <span className="text-sm text-gray-700">
            Авто-ремонт по pytest (архив, GitHub или отдельные файлы)
          </span>
        </label>
      </div>

      <button type="submit" className="btn-primary w-full" disabled={loading || submitBlocked}>
        {loading ? 'Генерация...' : submitBlocked ? 'Сначала удалите версию в истории' : 'Сгенерировать тесты'}
      </button>
    </form>
  );
}