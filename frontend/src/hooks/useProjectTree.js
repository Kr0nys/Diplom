import { useEffect, useState } from 'react';
import { sessionsAPI } from '../api/sessions';

/**
 * @param {string | undefined} sessionId
 * @param {object | null | undefined} session
 * @param {boolean} hasUploads
 */
export function useProjectTree(sessionId, session, hasUploads) {
  const [projectTree, setProjectTree] = useState(null);
  const [treeLoading, setTreeLoading] = useState(false);

  useEffect(() => {
    const fromMetrics = session?.metrics?.project_tree;
    if (fromMetrics?.children?.length) {
      setProjectTree(fromMetrics);
      setTreeLoading(false);
      return undefined;
    }

    if (!hasUploads || !sessionId) {
      setProjectTree(null);
      setTreeLoading(false);
      return undefined;
    }

    let cancelled = false;
    setTreeLoading(true);
    sessionsAPI
      .getProjectTree(sessionId)
      .then((data) => {
        if (!cancelled) setProjectTree(data?.tree || null);
      })
      .catch(() => {
        if (!cancelled) setProjectTree(null);
      })
      .finally(() => {
        if (!cancelled) setTreeLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [session?.metrics?.project_tree, sessionId, hasUploads]);

  return { projectTree, treeLoading };
}
