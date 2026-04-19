import { Link } from 'react-router-dom';
import StatusBadge from '../common/StatusBadge';
import { format } from 'date-fns';
import { ru } from 'date-fns/locale';

export default function SessionCard({ session }) {
  return (
    <div className="card hover:shadow-lg transition-shadow">
      <div className="flex justify-between items-start mb-4">
        <div>
          <h3 className="text-lg font-semibold text-gray-900">{session.name}</h3>
          <p className="text-sm text-gray-500">Python {session.python_version}</p>
        </div>
        <StatusBadge status={session.status} />
      </div>

      <div className="space-y-2 mb-4">
        <div className="flex justify-between text-sm">
          <span className="text-gray-600">Файлов:</span>
          <span className="font-medium">{session.file_count || 0}</span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-gray-600">Функций:</span>
          <span className="font-medium">{session.metrics?.functions_count || 0}</span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-gray-600">Классов:</span>
          <span className="font-medium">{session.metrics?.classes_count || 0}</span>
        </div>
      </div>

      <div className="flex justify-between items-center pt-4 border-t">
        <span className="text-xs text-gray-500">
          {format(new Date(session.created_at), 'dd MMM yyyy', { locale: ru })}
        </span>
        <Link
          to={`/sessions/${session.id}`}
          className="text-primary-600 hover:text-primary-700 text-sm font-medium"
        >
          Подробнее →
        </Link>
      </div>
    </div>
  );
}