/**
 * Единый скелетон контента вкладки сессии (ожидание метрик / данные подтягиваются).
 */
export default function SessionSectionSkeleton({ message = 'Загрузка данных…' }) {
  return (
    <div
      className="card space-y-4 animate-pulse"
      aria-busy="true"
      aria-live="polite"
    >
      <div className="flex justify-between gap-4">
        <div className="h-5 bg-gray-200 rounded w-2/5 max-w-xs" />
        <div className="h-5 bg-gray-200 rounded w-16 hidden sm:block" />
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="h-24 bg-gray-100 rounded-lg" />
        ))}
      </div>
      <div className="h-40 bg-gray-100 rounded-lg" />
      <div className="h-28 bg-gray-100 rounded-lg w-full md:w-4/5" />
      <p className="text-sm text-gray-500 animate-none">{message}</p>
    </div>
  );
}
