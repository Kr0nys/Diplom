/** Стандартный отступ Python в редакторе */
export const PYTHON_INDENT = '    ';

/**
 * @param {string} line
 * @returns {string}
 */
export function leadingWhitespace(line) {
  const m = line.match(/^(\s*)/);
  return m ? m[1] : '';
}

/**
 * Строка открывает блок (def, if, class, …) — после Enter нужен дополнительный отступ.
 * @param {string} line
 */
export function lineOpensBlock(line) {
  const trimmed = line.trim();
  if (!trimmed || trimmed.startsWith('#')) return false;
  const code = trimmed.split('#')[0].trimEnd();
  if (!code.endsWith(':')) return false;
  if (code === ':') return false;
  return true;
}

/**
 * @param {string} text
 * @param {number} pos
 */
function lineStartAt(text, pos) {
  const i = text.lastIndexOf('\n', Math.max(0, pos - 1));
  return i === -1 ? 0 : i + 1;
}

/**
 * @param {string} text
 * @param {number} pos
 */
function lineEndAt(text, pos) {
  const i = text.indexOf('\n', pos);
  return i === -1 ? text.length : i;
}

/**
 * @param {string} text
 * @param {number} start
 * @param {number} end
 */
function selectionLineRange(text, start, end) {
  const lineStart = lineStartAt(text, start);
  const lineEnd = lineEndAt(text, Math.max(start, end - 1));
  return { lineStart, lineEnd };
}

/**
 * @param {string} text
 * @param {number} start
 * @param {number} end
 * @param {boolean} unindent
 */
function indentLineBlock(text, start, end, unindent) {
  const { lineStart, lineEnd } = selectionLineRange(text, start, end);
  const block = text.slice(lineStart, lineEnd);
  const lines = block.split('\n');

  let removedFromFirst = 0;
  const newLines = lines.map((line, idx) => {
    if (unindent) {
      if (line.startsWith(PYTHON_INDENT)) {
        if (idx === 0) removedFromFirst = PYTHON_INDENT.length;
        return line.slice(PYTHON_INDENT.length);
      }
      if (line.startsWith('\t')) {
        if (idx === 0) removedFromFirst = 1;
        return line.slice(1);
      }
      const m = line.match(/^( +)/);
      if (m) {
        const n = Math.min(m[1].length, PYTHON_INDENT.length);
        if (idx === 0) removedFromFirst = n;
        return line.slice(n);
      }
      return line;
    }
    return PYTHON_INDENT + line;
  });

  const newBlock = newLines.join('\n');
  const newText = text.slice(0, lineStart) + newBlock + text.slice(lineEnd);
  const delta = newBlock.length - block.length;

  if (unindent) {
    return {
      value: newText,
      selectionStart: Math.max(lineStart, start - removedFromFirst),
      selectionEnd: Math.max(lineStart, end + delta),
    };
  }

  return {
    value: newText,
    selectionStart: start + PYTHON_INDENT.length,
    selectionEnd: end + delta,
  };
}

/**
 * @param {string} text
 * @param {number} cursor
 */
function unindentAtCursor(text, cursor) {
  const lineStart = lineStartAt(text, cursor);
  const line = text.slice(lineStart, cursor);
  const ws = leadingWhitespace(line);
  if (!ws) {
    return { value: text, selectionStart: cursor, selectionEnd: cursor };
  }
  const remove = Math.min(ws.length, PYTHON_INDENT.length, cursor - lineStart);
  const newText = text.slice(0, cursor - remove) + text.slice(cursor);
  return {
    value: newText,
    selectionStart: cursor - remove,
    selectionEnd: cursor - remove,
  };
}

/**
 * @param {string} text
 * @param {number} selectionStart
 * @param {number} selectionEnd
 */
function handleEnter(text, selectionStart, selectionEnd) {
  if (selectionStart !== selectionEnd) {
    const before = text.slice(0, selectionStart);
    const after = text.slice(selectionEnd);
    const newValue = `${before}\n${after}`;
    const pos = selectionStart + 1;
    return { value: newValue, selectionStart: pos, selectionEnd: pos };
  }

  const before = text.slice(0, selectionStart);
  const after = text.slice(selectionEnd);
  const lineStart = lineStartAt(text, selectionStart);
  const currentLine = before.slice(lineStart);
  const indent = leadingWhitespace(currentLine);
  const extra = lineOpensBlock(currentLine) ? PYTHON_INDENT : '';
  const insert = `\n${indent}${extra}`;
  const newValue = before + insert + after;
  const pos = before.length + insert.length;
  return { value: newValue, selectionStart: pos, selectionEnd: pos };
}

/**
 * @param {KeyboardEvent} e
 * @param {string} value
 * @param {number} selectionStart
 * @param {number} selectionEnd
 * @returns {{ value: string, selectionStart: number, selectionEnd: number } | null}
 */
export function applyPythonEditorKey(e, value, selectionStart, selectionEnd) {
  if (e.key === 'Tab') {
    e.preventDefault();
    if (selectionStart !== selectionEnd) {
      return indentLineBlock(value, selectionStart, selectionEnd, e.shiftKey);
    }
    if (e.shiftKey) {
      return unindentAtCursor(value, selectionStart);
    }
    const before = value.slice(0, selectionStart);
    const after = value.slice(selectionEnd);
    const newValue = before + PYTHON_INDENT + after;
    const pos = selectionStart + PYTHON_INDENT.length;
    return { value: newValue, selectionStart: pos, selectionEnd: pos };
  }

  if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
    e.preventDefault();
    return handleEnter(value, selectionStart, selectionEnd);
  }

  return null;
}
