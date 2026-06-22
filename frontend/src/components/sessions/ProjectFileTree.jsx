import { useCallback, useEffect, useMemo, useState } from 'react';

function formatSize(bytes) {
  if (bytes == null || Number.isNaN(Number(bytes))) return '';
  const n = Number(bytes);
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(n >= 10240 ? 0 : 1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function fileIcon(name = '') {
  const n = name.toLowerCase();
  if (n.endsWith('.py')) return '🐍';
  if (n.endsWith('.json') || n.endsWith('.yaml') || n.endsWith('.yml')) return '📋';
  if (n.endsWith('.md') || n.endsWith('.txt')) return '📄';
  if (n.endsWith('.zip') || n.endsWith('.tar') || n.endsWith('.gz')) return '📦';
  return '📄';
}

function TreeNode({ node, depth = 0, defaultOpen }) {
  const isFolder = node.type === 'folder';
  const [open, setOpen] = useState(defaultOpen);

  useEffect(() => {
    setOpen(defaultOpen);
  }, [defaultOpen]);

  const toggle = useCallback(() => {
    if (isFolder) setOpen((v) => !v);
  }, [isFolder]);

  const children = isFolder ? node.children || [] : [];
  const hasChildren = children.length > 0;

  return (
    <li className="select-none">
      <button
        type="button"
        onClick={toggle}
        className={`flex items-center gap-1.5 w-full text-left rounded px-1 py-0.5 text-sm hover:bg-gray-100 ${
          isFolder ? 'font-medium text-gray-800' : 'text-gray-700 font-normal'
        }`}
        style={{ paddingLeft: `${depth * 12 + 4}px` }}
        aria-expanded={isFolder ? open : undefined}
      >
        {isFolder ? (
          <span className="w-4 shrink-0 text-gray-500 text-xs" aria-hidden>
            {hasChildren ? (open ? '▾' : '▸') : '·'}
          </span>
        ) : (
          <span className="w-4 shrink-0" aria-hidden />
        )}
        <span className="shrink-0 text-xs" aria-hidden>
          {isFolder ? '📁' : fileIcon(node.name)}
        </span>
        <span className="truncate flex-1 min-w-0" title={node.path || node.name}>
          {node.name}
        </span>
        {!isFolder && node.size != null && (
          <span className="text-[10px] text-gray-400 shrink-0 tabular-nums">{formatSize(node.size)}</span>
        )}
      </button>
      {isFolder && open && hasChildren && (
        <ul className="border-l border-gray-200 ml-3">
          {children.map((child) => (
            <TreeNode
              key={`${child.type}:${child.path || child.name}`}
              node={child}
              depth={depth + 1}
              defaultOpen={depth < 1}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

/**
 * @param {{ tree: object | null, loading?: boolean, className?: string, embedded?: boolean }} props
 */
export default function ProjectFileTree({ tree, loading = false, className = '', embedded = false }) {
  const fileCount = useMemo(() => {
    let n = 0;
    const walk = (node) => {
      if (!node) return;
      if (node.type === 'file') n += 1;
      (node.children || []).forEach(walk);
    };
    walk(tree);
    return n;
  }, [tree]);

  const shellClass = embedded
    ? `flex flex-col min-h-0 border-t border-gray-200 pt-3 ${className}`
    : `card flex flex-col min-h-0 ${className}`;

  if (loading) {
    return (
      <div className={shellClass}>
        <h4 className="text-sm font-semibold text-gray-900 mb-2">Структура проекта</h4>
        <p className="text-sm text-gray-500">Загрузка дерева файлов…</p>
      </div>
    );
  }

  if (!tree?.children?.length) {
    return null;
  }

  const listMaxH = embedded ? 'max-h-[min(50vh,20rem)]' : 'max-h-[min(70vh,28rem)]';

  return (
    <div className={shellClass}>
      <div className={`shrink-0 ${embedded ? 'mb-2' : 'border-b pb-2 mb-2'}`}>
        <h4 className="text-sm font-semibold text-gray-900">Структура проекта</h4>
        <p className="text-xs text-gray-500 mt-0.5">{fileCount} файл(ов) в дереве</p>
      </div>
      <ul className={`overflow-auto ${listMaxH} pr-1 -mr-1 space-y-0.5 min-h-0`}>
        {(tree.children || []).map((child) => (
          <TreeNode
            key={`${child.type}:${child.path || child.name}`}
            node={child}
            depth={0}
            defaultOpen
          />
        ))}
      </ul>
    </div>
  );
}
