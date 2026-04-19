import { useState } from 'react';

export default function TestViewer({ code, onDownload }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="card">
      <div className="flex justify-between items-center mb-4">
        <h3 className="text-lg font-semibold">Сгенерированные тесты</h3>
        <div className="flex space-x-2">
          <button onClick={handleCopy} className="btn-secondary text-sm">
            {copied ? 'Скопировано!' : 'Копировать'}
          </button>
          <button onClick={onDownload} className="btn-primary text-sm">
            Скачать
          </button>
        </div>
      </div>

      <pre className="bg-gray-900 text-gray-100 p-4 rounded-lg overflow-x-auto text-sm font-mono max-h-96 overflow-y-auto">
        <code>{code}</code>
      </pre>
    </div>
  );
}