import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, PieChart, Pie, Cell } from 'recharts';

const COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'];

export default function AnalysisResults({ metrics, report }) {
  const functionData = [
    { name: 'Синхронные', value: (metrics?.functions_count || 0) - (metrics?.async_functions || 0) },
    { name: 'Асинхронные', value: metrics?.async_functions || 0 }
  ];

  return (
    <div className="space-y-6">
      {/* Карточки метрик */}
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

      {/* Графики */}
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

      {/* Текстовый отчет */}
      {report && (
        <div className="card">
          <h4 className="text-lg font-semibold mb-4">Отчет анализа</h4>
          <pre className="bg-gray-50 p-4 rounded-lg text-sm text-gray-700 whitespace-pre-wrap font-sans">
            {report}
          </pre>
        </div>
      )}
    </div>
  );
}