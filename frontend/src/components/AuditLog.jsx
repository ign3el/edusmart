import React, { useState, useEffect } from 'react';
import { RefreshCw, ChevronLeft, ChevronRight, History } from 'lucide-react';
import { getAdminAuditLog } from '../services/api';
import './AuditLog.css';

const PAGE_SIZE = 25;

function formatDate(iso) {
  return new Date(iso).toLocaleString();
}

const AuditLog = () => {
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [actionFilter, setActionFilter] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchLog = async (newOffset = offset) => {
    setLoading(true);
    setError(null);
    try {
      const params = { limit: PAGE_SIZE, offset: newOffset };
      if (actionFilter) params.action = actionFilter;
      const data = await getAdminAuditLog(params);
      setItems(data.items);
      setTotal(data.total);
      setOffset(newOffset);
    } catch (err) {
      setError('Failed to load audit log: ' + (err.response?.data?.detail || err.message));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchLog(0); }, [actionFilter]);

  const actions = [...new Set(items.map((i) => i.action))];

  return (
    <div className="audit-log">
      <div className="management-header">
        <h2>Audit Log</h2>
        <div className="header-actions">
          <select className="audit-filter" value={actionFilter} onChange={(e) => setActionFilter(e.target.value)}>
            <option value="">All actions</option>
            {actions.map((a) => <option key={a} value={a}>{a}</option>)}
          </select>
          <button className="refresh-btn" onClick={() => fetchLog(0)}><RefreshCw size={14} /> Refresh</button>
        </div>
      </div>
      {error && <div className="error-message">{error}</div>}
      {loading ? <div className="loading">Loading...</div> : (
        <>
          <div className="audit-table">
            {items.length === 0 && <div className="no-results">No admin actions recorded yet.</div>}
            {items.map((item) => (
              <div key={item.id} className="audit-row">
                <History size={13} className="audit-icon" />
                <div className="audit-main">
                  <div className="audit-line">
                    <span className="audit-actor">{item.actor_username || `user #${item.actor_user_id}`}</span>
                    <span className="audit-action">{item.action}</span>
                    {item.target_type && <span className="audit-target">{item.target_type}:{item.target_id}</span>}
                  </div>
                  {item.details && <div className="audit-details">{typeof item.details === 'string' ? item.details : JSON.stringify(item.details)}</div>}
                </div>
                <div className="audit-time">{formatDate(item.created_at)}</div>
              </div>
            ))}
          </div>
          <div className="audit-pagination">
            <button disabled={offset === 0} onClick={() => fetchLog(Math.max(0, offset - PAGE_SIZE))}><ChevronLeft size={16} /> Prev</button>
            <span>{offset + 1}-{Math.min(offset + PAGE_SIZE, total)} of {total}</span>
            <button disabled={offset + PAGE_SIZE >= total} onClick={() => fetchLog(offset + PAGE_SIZE)}>Next <ChevronRight size={16} /></button>
          </div>
        </>
      )}
    </div>
  );
};

export default AuditLog;
