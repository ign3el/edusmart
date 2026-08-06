import React, { useState, useEffect } from 'react';
import { RefreshCw, AlertTriangle, RotateCcw } from 'lucide-react';
import { getAdminContentReview } from '../services/api';
import './ContentReview.css';

function formatDate(iso) {
  return new Date(iso).toLocaleString();
}

const ContentReview = () => {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchItems = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getAdminContentReview();
      setItems(data.items);
    } catch (err) {
      setError('Failed to load content review: ' + (err.response?.data?.detail || err.message));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchItems(); }, []);

  if (loading) return <div className="loading">Loading failed generations...</div>;

  return (
    <div className="content-review">
      <div className="management-header">
        <h2>Content Review</h2>
        <button className="refresh-btn" onClick={fetchItems}><RefreshCw size={14} /> Refresh</button>
      </div>
      {error && <div className="error-message">{error}</div>}

      <div className="cr-list">
        {items.length === 0 && <div className="no-results">No failed generations right now.</div>}
        {items.map((item) => (
          <div key={item.story_id} className="cr-row">
            <AlertTriangle size={16} className="cr-icon" />
            <div className="cr-main">
              <div className="cr-line">
                <span className="cr-title">{item.title || item.story_id}</span>
                <span className="cr-code">{item.failure_code}</span>
                {item.can_retry && <span className="cr-retryable"><RotateCcw size={11} /> retryable</span>}
              </div>
              <div className="cr-message">{item.failure_message}</div>
              <div className="cr-meta">
                {item.username || `user #${item.user_id}`} · {formatDate(item.created_at)} · status: {item.status}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default ContentReview;
