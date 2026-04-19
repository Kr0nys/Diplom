import { useState } from 'react';
import { sessionsAPI } from '../../api/sessions';
import toast from 'react-hot-toast';

export default function TestGenerator({ sessionId, onTestsGenerated }) {
  const [loading, setLoading] = useState(false);
  const [config, setConfig] = useState({
    detail_level: 'advanced',
    use_mocks: true,
    include_edge_cases: true,
    test_framework: 'pytest',
    model: 'llama3.2'
  });

  const handleGenerate = async () => {
    setLoading(true);
    try {
      const result = await sessionsAPI.generateTests(sessionId, config);
      toast.success('Генерация тестов запущена!');

      // Polling статуса
      const pollInterval = setInterval(async () => {
        const status = await sessionsAPI.getSessionStatus(sessionId);
        if (status.status === 'TESTS_GENERATED') {
          clearInterval(pollInterval);
          toast.success('Тесты сгенерированы!');
          onTestsGenerated?.();
        } else if (status.status === 'FAILED') {
          clearInterval(pollInterval);
          toast.error('Ошибка генерации тестов');
        }
      }, 2000);

    } catch (error) {
      console.error('Generate error:', error);
      toast.error(error.response?.data?.error || 'Ошибка генерации');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-white rounded-lg shadow p-6">
      <h3 className="text-lg font-semibold mb-4">🤖 Генерация тестов с AI</h3>

      <div className="space-y-4">
        {/* Уровень детализации */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Уровень детализации
          </label>
          <select
            value={config.detail_level}
            onChange={(e) => setConfig({...config, detail_level: e.target.value})}
            className="w-full border rounded-md px-3 py-2"
          >
            <option value="basic">Basic (без AI, быстро)</option>
            <option value="advanced">Advanced (AI, с моками)</option>
            <option value="full">Full (AI, максимум тестов)</option>
          </select>
        </div>

        {/* Фреймворк */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Фреймворк
          </label>
          <select
            value={config.test_framework}
            onChange={(e) => setConfig({...config, test_framework: e.target.value})}
            className="w-full border rounded-md px-3 py-2"
          >
            <option value="pytest">pytest</option>
            <option value="unittest">unittest</option>
          </select>
        </div>

        {/* Опции */}
        <div className="space-y-2">
          <label className="flex items-center">
            <input
              type="checkbox"
              checked={config.use_mocks}
              onChange={(e) => setConfig({...config, use_mocks: e.target.checked})}
              className="mr-2"
            />
            <span className="text-sm">Использовать моки</span>
          </label>

          <label className="flex items-center">
            <input
              type="checkbox"
              checked={config.include_edge_cases}
              onChange={(e) => setConfig({...config, include_edge_cases: e.target.checked})}
              className="mr-2"
            />
            <span className="text-sm">Включить граничные случаи</span>
          </label>
        </div>

        {/* Кнопка */}
        <button
          onClick={handleGenerate}
          disabled={loading}
          className="w-full bg-blue-600 text-white py-2 px-4 rounded-md hover:bg-blue-700 disabled:bg-gray-400"
        >
          {loading ? '⏳ Генерация...' : '🚀 Сгенерировать тесты'}
        </button>
      </div>
    </div>
  );
}