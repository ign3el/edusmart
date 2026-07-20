import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Search, X, Plus, Shield, XCircle, Trash2, Save,
  Calendar, Mail, AtSign, KeyRound, BookOpen, ChevronLeft, ChevronRight, UserPlus
} from 'lucide-react';
import apiClient from '../services/api';
import { getItemsPerPage } from '../utils/responsiveUtils';
import Flip3DCard from './admin/Flip3DCard';
import './UserManagement.css';

const gridVariants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.05 } }
};

const cardVariants = {
  hidden: { opacity: 0, y: 16 },
  show: { opacity: 1, y: 0, transition: { duration: 0.3, ease: 'easeOut' } }
};

const emptyCreateState = { username: '', email: '', password: '', is_admin: false, is_verified: true };

function formatDate(dateString) {
  if (!dateString) return 'N/A';
  const date = new Date(dateString);
  return date.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

const UserManagement = () => {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [page, setPage] = useState(1);
  const [itemsPerPage, setItemsPerPage] = useState(getItemsPerPage(window.innerWidth));

  const [editState, setEditState] = useState({});
  const [savingId, setSavingId] = useState(null);
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState(emptyCreateState);
  const [creating, setCreating] = useState(false);
  const [showConfirmDelete, setShowConfirmDelete] = useState(null);

  useEffect(() => {
    fetchUsers();
    const handleResize = () => setItemsPerPage(getItemsPerPage(window.innerWidth));
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  useEffect(() => {
    setPage(1);
  }, [searchQuery]);

  const fetchUsers = async () => {
    try {
      setLoading(true);
      const response = await apiClient.get('/api/admin/users');
      if (response.data.success) {
        setUsers(response.data.users);
        setError(null);
      }
    } catch (err) {
      setError('Failed to fetch users: ' + (err.response?.data?.detail || err.message));
      console.error('Fetch users error:', err);
    } finally {
      setLoading(false);
    }
  };

  const getEdit = (user) => editState[user.id] || {
    username: user.username, email: user.email,
    is_admin: !!user.is_admin, is_verified: !!user.is_verified, is_premium: !!user.is_premium,
    password: ''
  };

  const setEdit = (user, patch) => {
    setEditState((prev) => ({ ...prev, [user.id]: { ...(prev[user.id] || getEdit(user)), ...patch } }));
  };

  const handleSave = async (user) => {
    const edit = getEdit(user);
    const payload = {};
    if (edit.username !== user.username) payload.username = edit.username;
    if (edit.email !== user.email) payload.email = edit.email;
    if (edit.is_admin !== !!user.is_admin) payload.is_admin = edit.is_admin;
    if (edit.is_verified !== !!user.is_verified) payload.is_verified = edit.is_verified;
    if (edit.is_premium !== !!user.is_premium) payload.is_premium = edit.is_premium;
    if (edit.password) payload.password = edit.password;

    if (Object.keys(payload).length === 0) return;

    setSavingId(user.id);
    setError(null);
    try {
      await apiClient.put(`/api/admin/users/${user.id}`, payload);
      setEditState((prev) => { const next = { ...prev }; delete next[user.id]; return next; });
      await fetchUsers();
    } catch (err) {
      setError('Update failed: ' + (err.response?.data?.detail || err.message));
    } finally {
      setSavingId(null);
    }
  };

  const handleDelete = async (user) => {
    try {
      await apiClient.delete(`/api/admin/users/${user.id}`);
      setShowConfirmDelete(null);
      await fetchUsers();
    } catch (err) {
      setError('Delete failed: ' + (err.response?.data?.detail || err.message));
    }
  };

  const handleCreate = async () => {
    if (!createForm.username.trim() || !createForm.email.trim() || createForm.password.length < 8) {
      setError('Username, email and an 8+ character password are required.');
      return;
    }
    setCreating(true);
    setError(null);
    try {
      await apiClient.post('/api/admin/users', createForm);
      setShowCreate(false);
      setCreateForm(emptyCreateState);
      await fetchUsers();
    } catch (err) {
      setError('Create failed: ' + (err.response?.data?.detail || err.message));
    } finally {
      setCreating(false);
    }
  };

  const filteredUsers = users.filter(u =>
    u.username.toLowerCase().includes(searchQuery.toLowerCase()) ||
    u.email.toLowerCase().includes(searchQuery.toLowerCase())
  );
  const totalPages = Math.max(1, Math.ceil(filteredUsers.length / itemsPerPage));
  const startIndex = (page - 1) * itemsPerPage;
  const paginatedUsers = filteredUsers.slice(startIndex, startIndex + itemsPerPage);

  if (loading) {
    return <div className="loading">Loading users...</div>;
  }

  return (
    <div className="user-management">
      <div className="management-header">
        <h2>User Management</h2>
        <div className="header-actions">
          <button className="add-user-btn" onClick={() => setShowCreate(true)}>
            <UserPlus size={16} /> Add User
          </button>
          <button className="refresh-btn" onClick={fetchUsers}>Refresh</button>
        </div>
      </div>

      {error && <div className="error-message">{error}</div>}

      <div className="search-container">
        <Search size={18} className="search-icon" />
        <input
          type="text"
          placeholder="Search by username or email..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="search-input"
        />
        {searchQuery && (
          <button className="clear-search" onClick={() => setSearchQuery('')} title="Clear search">
            <X size={16} />
          </button>
        )}
      </div>

      {filteredUsers.length > 0 && (
        <div className="results-info">
          Showing {startIndex + 1}-{Math.min(startIndex + itemsPerPage, filteredUsers.length)} of {filteredUsers.length} {filteredUsers.length === 1 ? 'user' : 'users'}
        </div>
      )}

      <motion.div className="users-grid" variants={gridVariants} initial="hidden" animate="show">
        {paginatedUsers.map((user) => {
          const edit = getEdit(user);
          return (
            <motion.div key={user.id} variants={cardVariants}>
              <Flip3DCard
                frontLabel={`View full details for ${user.username}`}
                backLabel="Back to summary"
                front={
                  <div className="user-card-front">
                    <div className="user-card-badges">
                      {user.is_admin && <span className="badge admin"><Shield size={11} /> Admin</span>}
                      {!user.is_verified && <span className="badge inactive"><XCircle size={11} /> Unverified</span>}
                    </div>
                    <h3>{user.username}</h3>
                    <p className="user-card-email"><Mail size={13} /> {user.email}</p>
                    <div className="user-card-stats">
                      <span><BookOpen size={13} /> {user.story_count || 0} stories</span>
                      <span><Calendar size={13} /> {formatDate(user.created_at)}</span>
                    </div>
                  </div>
                }
                back={
                  <div className="user-card-back">
                    <h4>Edit User</h4>
                    <label className="field-label">
                      <span className="field-label-text"><AtSign size={12} /> Username</span>
                      <input type="text" value={edit.username} onChange={(e) => setEdit(user, { username: e.target.value })} />
                    </label>
                    <label className="field-label">
                      <span className="field-label-text"><Mail size={12} /> Email</span>
                      <input type="email" value={edit.email} onChange={(e) => setEdit(user, { email: e.target.value })} />
                    </label>
                    <label className="field-label">
                      <span className="field-label-text"><KeyRound size={12} /> New password (optional)</span>
                      <input type="password" placeholder="Leave blank to keep current" value={edit.password} onChange={(e) => setEdit(user, { password: e.target.value })} />
                    </label>
                    <div className="toggle-row">
                      <label className="toggle-check">
                        <input type="checkbox" checked={edit.is_admin} onChange={(e) => setEdit(user, { is_admin: e.target.checked })} /> Admin
                      </label>
                      <label className="toggle-check">
                        <input type="checkbox" checked={edit.is_verified} onChange={(e) => setEdit(user, { is_verified: e.target.checked })} /> Verified
                      </label>
                      <label className="toggle-check">
                        <input type="checkbox" checked={edit.is_premium} onChange={(e) => setEdit(user, { is_premium: e.target.checked })} /> Premium
                      </label>
                    </div>
                    <div className="user-card-actions">
                      <button className="save-btn" onClick={() => handleSave(user)} disabled={savingId === user.id}>
                        <Save size={14} /> {savingId === user.id ? 'Saving...' : 'Save'}
                      </button>
                      <button className="delete-btn" onClick={() => setShowConfirmDelete(user)}>
                        <Trash2 size={14} /> Delete
                      </button>
                    </div>
                  </div>
                }
              />
            </motion.div>
          );
        })}
      </motion.div>

      {filteredUsers.length === 0 && (
        <div className="no-results">No users found{searchQuery && ` matching "${searchQuery}"`}</div>
      )}

      {totalPages > 1 && (
        <div className="pagination">
          <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1} className="pagination-btn">
            <ChevronLeft size={16} /> Previous
          </button>
          <span className="pagination-info">Page {page} of {totalPages}</span>
          <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page >= totalPages} className="pagination-btn">
            Next <ChevronRight size={16} />
          </button>
        </div>
      )}

      <AnimatePresence>
        {showCreate && (
          <motion.div className="modal-overlay" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={() => setShowCreate(false)}>
            <motion.div
              className="modal-content"
              initial={{ scale: 0.85, y: 30 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.85, y: 30 }}
              onClick={(e) => e.stopPropagation()}
            >
              <h3><Plus size={18} /> Add User</h3>
              <label className="field-label">
                <span className="field-label-text"><AtSign size={12} /> Username</span>
                <input type="text" value={createForm.username} onChange={(e) => setCreateForm(f => ({ ...f, username: e.target.value }))} autoFocus />
              </label>
              <label className="field-label">
                <span className="field-label-text"><Mail size={12} /> Email</span>
                <input type="email" value={createForm.email} onChange={(e) => setCreateForm(f => ({ ...f, email: e.target.value }))} />
              </label>
              <label className="field-label">
                <span className="field-label-text"><KeyRound size={12} /> Password</span>
                <input type="password" placeholder="Minimum 8 characters" value={createForm.password} onChange={(e) => setCreateForm(f => ({ ...f, password: e.target.value }))} />
              </label>
              <div className="toggle-row">
                <label className="toggle-check">
                  <input type="checkbox" checked={createForm.is_admin} onChange={(e) => setCreateForm(f => ({ ...f, is_admin: e.target.checked }))} /> Admin
                </label>
                <label className="toggle-check">
                  <input type="checkbox" checked={createForm.is_verified} onChange={(e) => setCreateForm(f => ({ ...f, is_verified: e.target.checked }))} /> Verified
                </label>
              </div>
              <div className="modal-buttons">
                <button className="cancel-btn" onClick={() => { setShowCreate(false); setCreateForm(emptyCreateState); }}>Cancel</button>
                <button className="confirm-btn" onClick={handleCreate} disabled={creating}>
                  {creating ? 'Creating...' : 'Create User'}
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {showConfirmDelete && (
          <motion.div className="modal-overlay" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={() => setShowConfirmDelete(null)}>
            <motion.div
              className="modal-content"
              initial={{ scale: 0.85, y: 30 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.85, y: 30 }}
              onClick={(e) => e.stopPropagation()}
            >
              <h3>Delete User</h3>
              <p>Are you sure you want to delete <strong>{showConfirmDelete.username}</strong> and all their stories? This action cannot be undone.</p>
              <div className="modal-buttons">
                <button className="cancel-btn" onClick={() => setShowConfirmDelete(null)}>Cancel</button>
                <button className="confirm-btn danger" onClick={() => handleDelete(showConfirmDelete)}>Delete</button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};

export default UserManagement;
