const statusColors = {
  PENDING: 'bg-yellow-100 text-yellow-800',
  PROCESSING: 'bg-blue-100 text-blue-800',
  ANALYZED: 'bg-green-100 text-green-800',
  TESTS_GENERATED: 'bg-purple-100 text-purple-800',
  FAILED: 'bg-red-100 text-red-800',
  COMPLETED: 'bg-green-100 text-green-800',
  GENERATING: 'bg-indigo-100 text-indigo-800'
};

const statusLabels = {
  PENDING: 'Ожидание',
  PROCESSING: 'Обработка',
  ANALYZED: 'Проанализировано',
  TESTS_GENERATED: 'Тесты созданы',
  FAILED: 'Ошибка',
  COMPLETED: 'Завершено',
  GENERATING: 'Генерация'
};

export default function StatusBadge({ status }) {
  const colorClass = statusColors[status] || 'bg-gray-100 text-gray-800';
  const label = statusLabels[status] || status;

  return (
    <span className={`px-2.5 py-0.5 rounded-full text-xs font-medium ${colorClass}`}>
      {label}
    </span>
  );
}