import { useState } from 'react';

export default function TestConfigForm({ onSubmit, loading }) {
  const [config, setConfig] = useState({
    detail_level: 'basic',
    use_mocks: true,
    async_support: false,
    timeout_seconds: 300,
    include_edge_cases: false
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
          <option value="basic">Базовый (быстро)</option>
          <option value="advanced">Продвинутый (рекомендуется)</option>
          <option value="full">Полный (максимальное покрытие)</option>
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
          <span className="text-sm text-gray-700">Использовать моки для внешних зависимостей</span>
        </label>

        <label className="flex items-center space-x-3">
          <input
            type="checkbox"
            checked={config.async_support}
            onChange={(e) => setConfig({ ...config, async_support: e.target.checked })}
            className="w-4 h-4 text-primary-600 rounded"
          />
          <span className="text-sm text-gray-700">Поддержка асинхронного кода</span>
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
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">
          Таймаут генерации: {config.timeout_seconds} сек
        </label>
        <input
          type="range"
          min="60"
          max="1800"
          step="60"
          value={config.timeout_seconds}
          onChange={(e) => setConfig({ ...config, timeout_seconds: parseInt(e.target.value) })}
          className="w-full"
        />
        <div className="flex justify-between text-xs text-gray-500">
          <span>1 мин</span>
          <span>30 мин</span>
        </div>
      </div>

      <button type="submit" className="btn-primary w-full" disabled={loading}>
        {loading ? 'Генерация...' : 'Сгенерировать тесты'}
      </button>
    </form>
  );
}