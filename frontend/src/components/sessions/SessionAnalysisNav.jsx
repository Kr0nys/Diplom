import { useState, useRef, useEffect } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import ProjectFileTree from './ProjectFileTree';

function navLinkClassDesktop({ isActive }) {
  const base =
    'rounded-md px-3 py-2 text-sm font-medium transition-colors xl:w-full inline-block xl:block';
  return `${base} ${
    isActive ? 'bg-primary-50 text-primary-800 ring-1 ring-primary-200' : 'text-gray-700 hover:bg-gray-100 hover:text-gray-900'
  }`;
}

function dropdownItemClass({ isActive }) {
  return `block w-full text-left px-4 py-2.5 text-sm font-medium transition-colors ${
    isActive ? 'bg-primary-50 text-primary-800' : 'text-gray-800 hover:bg-gray-50'
  }`;
}

function sectionTitle(pathname, basePath) {
  const p = pathname.replace(/\/$/, '');
  const b = basePath.replace(/\/$/, '');
  if (p === b) return 'Анализ';
  if (pathname.includes('/recommendation')) return 'Рекомендации';
  if (pathname.includes('/generation-history')) return 'История генераций';
  if (pathname.includes('/test-run')) return 'Запуск тестов';
  return 'Анализ';
}

/**
 * Десктоп (xl+): вертикальный блок «Разделы» справа + дерево проекта.
 * Мобильные: выпадающий список разделов и дерево под ним.
 *
 * @param {{ sessionId: string, session: object, variant?: 'mobile' | 'desktop', projectTree?: object | null, treeLoading?: boolean, showProjectTree?: boolean }} props
 */
export default function SessionAnalysisNav({
  sessionId,
  session,
  variant = 'desktop',
  projectTree = null,
  treeLoading = false,
  showProjectTree = false,
}) {
  const basePath = `/sessions/${sessionId}`;
  const location = useLocation();
  const hasMetrics = session?.status !== 'PENDING' && session?.metrics;
  const storedCount = Array.isArray(session?.stored_tests) ? session.stored_tests.length : 0;
  const hasUploads = Boolean(
    session &&
      ((session.uploads_count ?? 0) > 0 ||
        (Array.isArray(session.uploaded_files) && session.uploaded_files.length > 0))
  );
  const canRunTests =
    hasUploads &&
    session?.status !== 'PENDING' &&
    session?.status !== 'PROCESSING';

  const [menuOpen, setMenuOpen] = useState(false);
  const wrapRef = useRef(null);

  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    if (!menuOpen || variant !== 'mobile') return undefined;
    const onKey = (e) => {
      if (e.key === 'Escape') setMenuOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [menuOpen, variant]);

  useEffect(() => {
    if (!menuOpen || variant !== 'mobile') return undefined;
    const onPointerDown = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('touchstart', onPointerDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('touchstart', onPointerDown);
    };
  }, [menuOpen, variant]);

  const linkClassDesktop = navLinkClassDesktop;
  const showTree = showProjectTree && (treeLoading || projectTree?.children?.length);

  if (variant === 'mobile') {
    const title = sectionTitle(location.pathname, basePath);
    return (
      <div ref={wrapRef} className="relative xl:hidden mb-4 space-y-4">
        <nav aria-label="Разделы сессии">
          <button
            type="button"
            className="btn-secondary flex items-center gap-2 w-full max-w-md justify-between text-left"
            aria-expanded={menuOpen}
            aria-haspopup="menu"
            aria-controls="session-sections-menu-mobile"
            id="session-sections-trigger-mobile"
            onClick={() => setMenuOpen((v) => !v)}
          >
            <span className="flex flex-col items-start gap-0.5 min-w-0">
              <span className="text-[10px] font-semibold uppercase tracking-wide text-gray-500 leading-none">
                Раздел
              </span>
              <span className="font-medium text-gray-900 truncate">{title}</span>
            </span>
            <svg
              className={`w-5 h-5 shrink-0 text-gray-600 transition-transform ${menuOpen ? 'rotate-180' : ''}`}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              aria-hidden
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </button>

          {menuOpen && (
            <div
              id="session-sections-menu-mobile"
              role="menu"
              aria-labelledby="session-sections-trigger-mobile"
              className="absolute left-0 right-0 mt-1 rounded-lg border border-gray-200 bg-white py-1 shadow-lg z-40 max-h-[70vh] overflow-auto"
            >
              <ul className="py-1">
                <li role="none">
                  <NavLink
                    to={basePath}
                    end
                    role="menuitem"
                    className={dropdownItemClass}
                    onClick={() => setMenuOpen(false)}
                  >
                    Анализ
                  </NavLink>
                </li>
                {hasMetrics && (
                  <li role="none">
                    <NavLink
                      to={`${basePath}/recommendation`}
                      role="menuitem"
                      className={dropdownItemClass}
                      onClick={() => setMenuOpen(false)}
                    >
                      Рекомендации
                    </NavLink>
                  </li>
                )}
                {storedCount > 0 && (
                  <li role="none">
                    <NavLink
                      to={`${basePath}/generation-history`}
                      role="menuitem"
                      className={dropdownItemClass}
                      onClick={() => setMenuOpen(false)}
                    >
                      История генераций
                    </NavLink>
                  </li>
                )}
                {canRunTests && (
                  <li role="none">
                    <NavLink
                      to={`${basePath}/test-run`}
                      role="menuitem"
                      className={dropdownItemClass}
                      onClick={() => setMenuOpen(false)}
                    >
                      Запуск тестов
                    </NavLink>
                  </li>
                )}
              </ul>
            </div>
          )}
        </nav>
        {showTree && (
          <ProjectFileTree tree={projectTree} loading={treeLoading} embedded />
        )}
      </div>
    );
  }

  return (
    <aside
      aria-label="Панель сессии"
      className="hidden xl:flex xl:flex-col xl:w-64 shrink-0 xl:sticky xl:top-24 xl:self-start xl:rounded-lg xl:border xl:border-gray-200 xl:bg-white xl:p-4 xl:shadow-sm xl:order-2 xl:max-h-[calc(100vh-7rem)]"
    >
      <nav aria-label="Разделы сессии" className="shrink-0">
      <p className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-3">Разделы</p>
      <ul className="flex flex-col gap-1">
        <li>
          <NavLink to={basePath} end className={linkClassDesktop}>
            Анализ
          </NavLink>
        </li>
        {hasMetrics && (
          <li>
            <NavLink to={`${basePath}/recommendation`} className={linkClassDesktop}>
              Рекомендации
            </NavLink>
          </li>
        )}
        {storedCount > 0 && (
          <li>
            <NavLink to={`${basePath}/generation-history`} className={linkClassDesktop}>
              История генераций
            </NavLink>
          </li>
        )}
        {canRunTests && (
          <li>
            <NavLink to={`${basePath}/test-run`} className={linkClassDesktop}>
              Запуск тестов
            </NavLink>
          </li>
        )}
      </ul>
      </nav>
      {showTree && (
        <ProjectFileTree
          tree={projectTree}
          loading={treeLoading}
          embedded
          className="mt-4 min-h-0 flex-1"
        />
      )}
    </aside>
  );
}
