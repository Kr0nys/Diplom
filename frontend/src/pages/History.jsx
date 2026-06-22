import { Link } from 'react-router-dom';
import { useState, useEffect } from 'react';
import { sessionsAPI } from '../api/sessions';
import { format } from 'date-fns';
import { ru } from 'date-fns/locale';
import StatusBadge from '../components/common/StatusBadge';
import LoadingSpinner from '../components/common/LoadingSpinner';
import toast from 'react-hot-toast';

function SessionHistoryCard({ session, onDelete }) {
  return (
    <div className="card space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-gray-900 truncate">{session.name}</h2>
          <p className="text-sm text-gray-500">Python {session.python_version}</p>
        </div>
        <StatusBadge status={session.status} />
      </div>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
        <div>
          <dt className="text-gray-500">Дата</dt>
          <dd className="font-medium text-gray-900">
            {format(new Date(session.created_at), 'dd MMM yyyy HH:mm', { locale: ru })}
          </dd>
        </div>
        <div>
          <dt className="text-gray-500">Файлов</dt>
          <dd className="font-medium text-gray-900">{session.files_count ?? 0}</dd>
        </div>
      </dl>

      <div className="flex gap-3 pt-1 border-t border-gray-100">
        <Link
          to={`/sessions/${session.id}`}
          className="btn-primary flex-1 text-center text-sm py-2"
        >
          Просмотр
        </Link>
        <button
          type="button"
          onClick={() => onDelete(session.id)}
          className="btn-secondary flex-1 text-sm py-2 text-red-600 hover:text-red-700"
        >
          Удалить
        </button>
      </div>
    </div>
  );
}

export default function History() {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadHistory();
  }, []);

  const loadHistory = async () => {
    try {
      const data = await sessionsAPI.getAll();
      setSessions(data.results || data);
    } catch (error) {
      toast.error('Ошибка загрузки истории');
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (id) => {
    if (!confirm('Вы уверены?')) return;

    try {
      await sessionsAPI.delete(id);
      setSessions(sessions.filter((s) => s.id !== id));
      toast.success('Сессия удалена');
    } catch (error) {
      toast.error('Ошибка удаления');
    }
  };

  if (loading) {
    return <LoadingSpinner />;
  }

  return (
    <div className="space-y-4 sm:space-y-6">
      <h1 className="text-xl sm:text-2xl font-bold text-gray-900">История сессий</h1>

      {sessions.length === 0 ? (
        <div className="card text-center py-12 text-gray-500">История пуста</div>
      ) : (
        <>
          <div className="md:hidden space-y-3">
            {sessions.map((session) => (
              <SessionHistoryCard
                key={session.id}
                session={session}
                onDelete={handleDelete}
              />
            ))}
          </div>

          <div className="hidden md:block card overflow-hidden">
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                      Название
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                      Статус
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                      Дата
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                      Файлов
                    </th>
                    <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase">
                      Действия
                    </th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {sessions.map((session) => (
                    <tr key={session.id} className="hover:bg-gray-50">
                      <td className="px-6 py-4 whitespace-nowrap">
                        <div className="text-sm font-medium text-gray-900">{session.name}</div>
                        <div className="text-sm text-gray-500">Python {session.python_version}</div>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
                        <StatusBadge status={session.status} />
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                        {format(new Date(session.created_at), 'dd MMM yyyy HH:mm', { locale: ru })}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                        {session.files_count ?? 0}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-right text-sm font-medium">
                        <Link
                          to={`/sessions/${session.id}`}
                          className="text-primary-600 hover:text-primary-900 mr-4"
                        >
                          Просмотр
                        </Link>
                        <button
                          type="button"
                          onClick={() => handleDelete(session.id)}
                          className="text-red-600 hover:text-red-900"
                        >
                          Удалить
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
