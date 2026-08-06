import React, { useState, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { RefreshCw, ToggleLeft, ToggleRight, Plus, Megaphone } from 'lucide-react';
import {
  getAdminConfigFlags, updateAdminConfigFlag,
  getAdminAnnouncements, createAdminAnnouncement, updateAdminAnnouncement,
} from '../services/api';
import './FeatureFlags.css';

function formatDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString();
}

const emptyAnnouncement = { message: '', severity: 'info', is_active: true };

const FeatureFlags = () => {
  const [flags, setFlags] = useState([]);
  const [announcements, setAnnouncements] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [savingKey, setSavingKey] = useState(null);
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState(emptyAnnouncement);
  const [creating, setCreating] = useState(false);

  const fetchAll = async () => {
    setLoading(true);
    setError(null);
    try {
      const [f, a] = await Promise.all([getAdminConfigFlags(), getAdminAnnouncements()]);
      setFlags(f.items);
      setAnnouncements(a.items);
    } catch (err) {
      setError('Failed to load: ' + (err.response?.data?.detail || err.message));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchAll(); }, []);

  const toggleFlag = async (flag) => {
    setSavingKey(flag.config_key);
    try {
      const next = flag.config_value.toLowerCase() === 'true' ? 'false' : 'true';
      await updateAdminConfigFlag(flag.config_key, next);
      await fetchAll();
    } catch (err) {
      setError('Update failed: ' + (err.response?.data?.detail || err.message));
    } finally {
      setSavingKey(null);
    }
  };

  const toggleAnnouncement = async (a) => {
    setSavingKey(`ann-${a.id}`);
    try {
      await updateAdminAnnouncement(a.id, { is_active: !a.is_active });
      await fetchAll();
    } catch (err) {
      setError('Update failed: ' + (err.response?.data?.detail || err.message));
    } finally {
      setSavingKey(null);
    }
  };

  const handleCreateAnnouncement = async () => {
    if (!createForm.message.trim()) {
      setError('A message is required.');
      return;
    }
    setCreating(true);
    setError(null);
    try {
      await createAdminAnnouncement({ ...createForm, message: createForm.message.trim() });
      setShowCreate(false);
      setCreateForm(emptyAnnouncement);
      await fetchAll();
    } catch (err) {
      setError('Create failed: ' + (err.response?.data?.detail || err.message));
    } finally {
      setCreating(false);
    }
  };

  if (loading) return <div className="loading">Loading feature flags...</div>;

  return (
    <div className="feature-flags">
      <div className="management-header">
        <h2>Feature Flags &amp; Announcements</h2>
        <button className="refresh-btn" onClick={fetchAll}><RefreshCw size={14} /> Refresh</button>
      </div>
      {error && <div className="error-message">{error}</div>}

      <h3 className="ff-section-title">Feature Flags</h3>
      <div className="ff-list">
        {flags.map((flag) => {
          const on = flag.config_value.toLowerCase() === 'true';
          return (
            <div key={flag.config_key} className="ff-row">
              <div className="ff-info">
                <div className="ff-key">{flag.config_key}</div>
                <div className="ff-desc">{flag.description}</div>
                <div className="ff-meta">Last changed: {formatDate(flag.updated_at)}</div>
              </div>
              <button className={`ff-toggle ${on ? 'on' : 'off'}`} onClick={() => toggleFlag(flag)} disabled={savingKey === flag.config_key}>
                {on ? <ToggleRight size={22} /> : <ToggleLeft size={22} />}
                {on ? 'Enabled' : 'Disabled'}
              </button>
            </div>
          );
        })}
      </div>

      <div className="management-header" style={{ marginTop: '1.5rem' }}>
        <h3 className="ff-section-title" style={{ margin: 0 }}>Site Announcements</h3>
        <button className="add-user-btn" onClick={() => setShowCreate(true)}><Plus size={16} /> New Announcement</button>
      </div>
      <div className="ff-list">
        {announcements.length === 0 && <div className="no-results">No announcements yet.</div>}
        {announcements.map((a) => (
          <div key={a.id} className={`ff-row ann-${a.severity}`}>
            <div className="ff-info">
              <div className="ff-key"><Megaphone size={13} /> {a.message}</div>
              <div className="ff-meta">{a.severity} · created {formatDate(a.created_at)}</div>
            </div>
            <button className={`ff-toggle ${a.is_active ? 'on' : 'off'}`} onClick={() => toggleAnnouncement(a)} disabled={savingKey === `ann-${a.id}`}>
              {a.is_active ? <ToggleRight size={22} /> : <ToggleLeft size={22} />}
              {a.is_active ? 'Live' : 'Off'}
            </button>
          </div>
        ))}
      </div>

      {showCreate && createPortal(
        <div className="modal-overlay" onClick={() => setShowCreate(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <h3><Plus size={18} /> New Announcement</h3>
            <label className="field-label">
              <span className="field-label-text">Message</span>
              <input placeholder="We're aware of slow image generation right now" value={createForm.message}
                onChange={(e) => setCreateForm(f => ({ ...f, message: e.target.value }))} autoFocus maxLength={500} />
            </label>
            <label className="field-label">
              <span className="field-label-text">Severity</span>
              <select value={createForm.severity} onChange={(e) => setCreateForm(f => ({ ...f, severity: e.target.value }))}>
                <option value="info">Info</option>
                <option value="warning">Warning</option>
                <option value="critical">Critical</option>
              </select>
            </label>
            <label className="field-label ff-checkbox-label">
              <input type="checkbox" checked={createForm.is_active} onChange={(e) => setCreateForm(f => ({ ...f, is_active: e.target.checked }))} />
              <span className="field-label-text">Make live immediately</span>
            </label>
            <div className="modal-buttons">
              <button className="cancel-btn" onClick={() => { setShowCreate(false); setCreateForm(emptyAnnouncement); }}>Cancel</button>
              <button className="confirm-btn" onClick={handleCreateAnnouncement} disabled={creating}>{creating ? 'Creating...' : 'Create'}</button>
            </div>
          </div>
        </div>,
        document.body
      )}
    </div>
  );
};

export default FeatureFlags;
