import { Outlet, Link, NavLink, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';

const NAV_ITEMS = [
  { path: '/dashboard', label: 'Дашборд', shortLabel: 'Дашборд' },
  { path: '/upload', label: 'Загрузка', shortLabel: 'Загрузка' },
  { path: '/history', label: 'История', shortLabel: 'История' },
];

function isNavItemActive(path, pathname) {
  if (path === '/dashboard') {
    return (
      pathname === '/dashboard' ||
      pathname === '/' ||
      pathname.startsWith('/sessions/')
    );
  }
  return pathname === path || pathname.startsWith(`${path}/`);
}

function NavIcon({ name, className = 'w-6 h-6' }) {
  const icons = {
    dashboard: (
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6"
      />
    ),
    upload: (
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"
      />
    ),
    history: (
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"
      />
    ),
  };
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
      {icons[name]}
    </svg>
  );
}

function navIconName(path) {
  if (path === '/dashboard') return 'dashboard';
  if (path === '/upload') return 'upload';
  return 'history';
}

export default function Layout() {
  const { user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col">
      <header className="bg-white shadow shrink-0 z-30">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex justify-between items-center h-14 sm:h-16 gap-3">
            <div className="flex items-center min-w-0 flex-1">
              <h1 className="text-sm sm:text-xl font-bold text-primary-600 leading-tight min-w-0">
                Python Test Gen
              </h1>

              <nav className="hidden sm:ml-6 sm:flex sm:space-x-8" aria-label="Основные разделы">
                {NAV_ITEMS.map((item) => (
                  <Link
                    key={item.path}
                    to={item.path}
                    className={`inline-flex items-center px-1 pt-1 border-b-2 text-sm font-medium ${
                      isNavItemActive(item.path, location.pathname)
                        ? 'border-primary-500 text-gray-900'
                        : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700'
                    }`}
                  >
                    {item.label}
                  </Link>
                ))}
              </nav>
            </div>

            <div className="flex items-center gap-2 sm:gap-4 shrink-0">
              <span className="hidden sm:inline text-sm text-gray-700">{user?.username}</span>
              <button type="button" onClick={handleLogout} className="btn-secondary text-sm py-2 px-3">
                Выйти
              </button>
            </div>
          </div>
        </div>
      </header>

      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-6 sm:py-8 pb-24 sm:pb-8">
        <Outlet />
      </main>

      <nav
        className="sm:hidden fixed bottom-0 inset-x-0 z-40 bg-white border-t border-gray-200 shadow-[0_-4px_6px_-1px_rgba(0,0,0,0.05)]"
        aria-label="Основные разделы"
        style={{ paddingBottom: 'env(safe-area-inset-bottom, 0px)' }}
      >
        <ul className="flex justify-around items-stretch max-w-lg mx-auto">
          {NAV_ITEMS.map((item) => {
            const active = isNavItemActive(item.path, location.pathname);
            return (
              <li key={item.path} className="flex-1">
                <NavLink
                  to={item.path}
                  className={`flex flex-col items-center justify-center gap-0.5 py-2.5 px-1 min-h-[3.25rem] text-xs font-medium transition-colors ${
                    active ? 'text-primary-600' : 'text-gray-500 hover:text-gray-800'
                  }`}
                >
                  <NavIcon
                    name={navIconName(item.path)}
                    className={`w-6 h-6 ${active ? 'text-primary-600' : 'text-gray-400'}`}
                  />
                  <span>{item.shortLabel}</span>
                </NavLink>
              </li>
            );
          })}
        </ul>
      </nav>
    </div>
  );
}
