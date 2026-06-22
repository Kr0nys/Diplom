import { useState, useEffect, useLayoutEffect, useCallback, useRef, useMemo } from 'react';
import { sessionsAPI } from '../../api/sessions';
import toast from 'react-hot-toast';
import { applyPythonEditorKey } from '../../utils/pythonEditorKeys';
import { copyTextToClipboard } from '../../utils/copyToClipboard';

const MONO = {
  fontSize: '13px',
  lineHeight: '22px',
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
};

function splitLines(text) {
  const s = text ?? '';
  return s === '' ? [''] : s.split('\n');
}

/**
 * Компактное окно кода фиксированной высоты: прокрутка только внутри textarea,
 * gutter синхронизируется по scrollTop (режимы просмотра и редактирования одинаковые по размеру).
 */
function LineNumberedTextarea({
  value,
  onChange,
  readOnly = false,
  maxHeight = null,
}) {
  const taRef = useRef(null);
  const gutterRef = useRef(null);
  /** @type {React.MutableRefObject<{ start: number, end: number } | null>} */
  const pendingSelectionRef = useRef(null);
  const lines = useMemo(() => splitLines(value), [value]);
  const digitCount = Math.max(2, String(lines.length).length);
  /** +2ch к ширине поля цифр, чтобы номера не прилипали к разделителю */
  const gutterWidth = `${digitCount + 2}ch`;

  const syncScrollFromTextarea = () => {
    if (gutterRef.current && taRef.current) {
      gutterRef.current.scrollTop = taRef.current.scrollTop;
    }
  };

  const syncScrollFromGutter = () => {
    if (gutterRef.current && taRef.current) {
      taRef.current.scrollTop = gutterRef.current.scrollTop;
    }
  };

  useLayoutEffect(() => {
    const sel = pendingSelectionRef.current;
    if (!sel || !taRef.current) return;
    taRef.current.selectionStart = sel.start;
    taRef.current.selectionEnd = sel.end;
    pendingSelectionRef.current = null;
  }, [value]);

  const handleKeyDown = (e) => {
    if (readOnly || !onChange) return;
    const ta = taRef.current;
    if (!ta) return;

    const result = applyPythonEditorKey(e, value, ta.selectionStart, ta.selectionEnd);
    if (!result) return;

    pendingSelectionRef.current = {
      start: result.selectionStart,
      end: result.selectionEnd,
    };
    onChange({ target: { value: result.value } });
  };

  const lh = MONO.lineHeight;

  /** Фиксированная высота области; без неё gutter растягивает страницу на тысячи строк */
  const viewHeight = maxHeight ?? '24rem';

  const textareaStyle = {
    ...MONO,
    height: '100%',
    minHeight: 0,
    maxHeight: 'none',
    resize: 'none',
    overflowY: 'auto',
  };

  return (
    <div
      className={`flex rounded-lg border border-gray-700 overflow-hidden bg-gray-900 min-h-0 ${
        readOnly ? '' : 'focus-within:ring-2 focus-within:ring-primary-500'
      }`}
      style={{
        height: `min(100vh, ${viewHeight})`,
        minHeight: '12rem',
      }}
    >
      <div
        ref={gutterRef}
        onScroll={syncScrollFromGutter}
        className="flex-shrink-0 py-3 pl-3 pr-2 text-right tabular-nums select-none text-gray-500 border-r border-gray-700 bg-gray-900 min-h-0 h-full overflow-y-auto overflow-x-hidden [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden"
        style={{ ...MONO, width: gutterWidth, minWidth: gutterWidth }}
      >
        {lines.map((_, i) => (
          <div key={i} style={{ lineHeight: lh, minHeight: lh }}>
            {i + 1}
          </div>
        ))}
      </div>
      <textarea
        ref={taRef}
        value={value}
        readOnly={readOnly}
        onChange={readOnly ? undefined : onChange}
        onKeyDown={readOnly ? undefined : handleKeyDown}
        onScroll={syncScrollFromTextarea}
        spellCheck={false}
        className={`flex-1 min-w-0 min-h-0 py-3 px-3 bg-gray-900 text-gray-100 border-0 focus:outline-none focus:ring-0 text-sm resize-none ${
          readOnly ? 'cursor-default' : ''
        }`}
        style={textareaStyle}
        aria-readonly={readOnly || undefined}
      />
    </div>
  );
}

/**
 * @param {object} props
 * @param {string} props.sessionId
 * @param {string} props.code — текст тестов
 * @param {() => void} [props.onDownload] — при compact не используется
 * @param {() => Promise<void>} [props.onSaved] — после успешного сохранения (обновить сессию)
 * @param {string} [props.storedId] — если правка записи из истории
 * @param {boolean} [props.compact] — компактный режим (история): без заголовка «Сгенерированные»
 * @param {(text: string) => void} [props.onActiveTextChange] — текущий текст (черновик или сохранённый)
 */
export default function TestViewer({
  sessionId,
  code,
  onDownload,
  onSaved,
  storedId = null,
  compact = false,
  onActiveTextChange = null,
}) {
  const [copied, setCopied] = useState(false);
  const [editMode, setEditMode] = useState(false);
  const [draft, setDraft] = useState(code ?? '');
  const [saving, setSaving] = useState(false);
  const [checking, setChecking] = useState(false);
  /** @type {[object|null, function]} */
  const [syntaxIssue, setSyntaxIssue] = useState(null);

  useEffect(() => {
    if (!editMode) {
      setDraft(code ?? '');
    }
  }, [code, editMode]);

  useEffect(() => {
    onActiveTextChange?.(editMode ? draft : code ?? '');
  }, [editMode, draft, code, onActiveTextChange]);

  const handleCopy = async () => {
    const text = editMode ? draft : code ?? '';
    const ok = await copyTextToClipboard(text);
    if (ok) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } else {
      toast.error('Не удалось скопировать (разрешите доступ к буферу обмена)');
    }
  };

  const handleCancelEdit = useCallback(() => {
    setDraft(code ?? '');
    setEditMode(false);
    setSyntaxIssue(null);
  }, [code]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await sessionsAPI.patchGeneratedTests(sessionId, {
        generated_tests: draft,
        ...(storedId ? { stored_id: storedId } : {}),
      });
      toast.success('Изменения сохранены');
      setEditMode(false);
      setSyntaxIssue(null);
      if (onSaved) await onSaved();
    } catch (error) {
      const msg =
        error?.response?.data?.error ||
        error?.response?.data?.detail ||
        error?.message ||
        'Не удалось сохранить';
      toast.error(typeof msg === 'string' ? msg : 'Не удалось сохранить');
    } finally {
      setSaving(false);
    }
  };

  /** Проверяем ровно тот текст, что видит пользователь (черновик или сохранённый). */
  const handleValidate = async () => {
    const text = editMode ? draft : code ?? '';
    if (!text.trim()) {
      toast.error('Нет текста для проверки');
      return;
    }
    setChecking(true);
    try {
      const result = await sessionsAPI.validateTests(sessionId, { generated_tests: text });
      if (result.valid) {
        setSyntaxIssue(null);
        toast.success(result.message || 'Синтаксис корректен');
      } else {
        setSyntaxIssue(result);
        toast.error(result.message || 'Ошибка синтаксиса', { duration: 8000 });
      }
    } catch (error) {
      setSyntaxIssue(null);
      toast.error(error?.response?.data?.error || 'Ошибка проверки');
    } finally {
      setChecking(false);
    }
  };

  const title = compact ? 'Код этой версии' : 'Сгенерированные тесты';

  return (
    <div className="card">
      <div className="flex flex-wrap justify-between items-center gap-2 mb-4">
        <h3 className="text-lg font-semibold">{title}</h3>
        <div className="flex flex-wrap gap-2">
          {!editMode ? (
            <button type="button" onClick={() => setEditMode(true)} className="btn-secondary text-sm">
              Редактировать
            </button>
          ) : (
            <>
              <button
                type="button"
                onClick={handleSave}
                disabled={saving}
                className="btn-primary text-sm"
              >
                {saving ? 'Сохранение…' : 'Сохранить'}
              </button>
              <button
                type="button"
                onClick={handleCancelEdit}
                disabled={saving}
                className="btn-secondary text-sm"
              >
                Отмена
              </button>
            </>
          )}
          <button type="button" onClick={handleCopy} className="btn-secondary text-sm">
            {copied ? 'Скопировано!' : 'Копировать'}
          </button>
          <button
            type="button"
            onClick={handleValidate}
            disabled={checking || !(editMode ? draft : code)?.trim()}
            className="btn-secondary text-sm"
          >
            {checking ? 'Проверка…' : 'Проверить синтаксис'}
          </button>
          {onDownload && (
            <button type="button" onClick={onDownload} className="btn-primary text-sm">
              Скачать
            </button>
          )}
        </div>
      </div>

      {syntaxIssue && syntaxIssue.valid === false && (
        <div className="mb-3 rounded-lg border border-red-200 bg-red-50 p-3 text-sm">
          <div className="flex justify-between gap-2 items-start">
            <div>
              <p className="font-semibold text-red-900">{syntaxIssue.message}</p>
              {syntaxIssue.error_type && (
                <p className="text-xs text-red-700 mt-1">Тип: {syntaxIssue.error_type}</p>
              )}
            </div>
            <button
              type="button"
              className="btn-secondary text-xs shrink-0"
              onClick={() => setSyntaxIssue(null)}
            >
              Скрыть
            </button>
          </div>
          {syntaxIssue.problem_line !== undefined && syntaxIssue.problem_line !== '' && (
            <div className="mt-3">
              <p className="text-xs font-medium text-red-800 mb-1">Строка с ошибкой:</p>
              <pre className="font-mono text-xs bg-gray-900 text-gray-100 p-3 rounded overflow-x-auto whitespace-pre">
                {syntaxIssue.problem_line}
                {syntaxIssue.pointer_line ? `\n${syntaxIssue.pointer_line}` : ''}
              </pre>
            </div>
          )}
          {Array.isArray(syntaxIssue.context) && syntaxIssue.context.length > 0 && (
            <div className="mt-3">
              <p className="text-xs font-medium text-red-800 mb-1">Контекст (соседние строки):</p>
              <div className="font-mono text-xs bg-white border border-red-100 rounded overflow-x-auto">
                {syntaxIssue.context.map((row) => (
                  <div
                    key={row.number}
                    className={`flex gap-2 px-2 py-0.5 border-b border-gray-100 last:border-0 ${
                      row.is_error_line ? 'bg-amber-100' : ''
                    }`}
                  >
                    <span className="text-gray-400 w-8 shrink-0 text-right select-none">{row.number}</span>
                    <span className="text-gray-900 whitespace-pre-wrap break-all">{row.content}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {editMode ? (
        <LineNumberedTextarea value={draft} onChange={(e) => setDraft(e.target.value)} />
      ) : (
        <LineNumberedTextarea value={code ?? ''} readOnly />
      )}
    </div>
  );
}
