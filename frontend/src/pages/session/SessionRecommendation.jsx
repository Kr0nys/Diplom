import { useOutletContext } from 'react-router-dom';
import AnalysisResults from '../../components/sessions/AnalysisResults';
import SessionSectionSkeleton from '../../components/sessions/SessionSectionSkeleton';

export default function SessionRecommendation() {
  const { session, metricsPending } = useOutletContext();

  if (metricsPending) {
    return <SessionSectionSkeleton message="Ждём результаты анализа…" />;
  }

  if (!session.metrics) {
    return (
      <div className="card text-gray-600 text-sm">
        Для этой сессии метрики недоступны. Откройте вкладку позже или вернитесь на «Анализ».
      </div>
    );
  }

  return <AnalysisResults metrics={session.metrics} report={session.report_text} variant="recommendations" />;
}
