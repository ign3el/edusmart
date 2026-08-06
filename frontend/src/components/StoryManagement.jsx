import React, { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import {
  Search, X, Play, Trash2, Save, Calendar, User, Mail, Hash, Layers,
  ChevronLeft, ChevronRight, Pencil, CheckCircle2, XCircle, Gauge, Activity
} from 'lucide-react';
import apiClient from '../services/api';
import { getItemsPerPage } from '../utils/responsiveUtils';
import Flip3DCard from './admin/Flip3DCard';
import './StoryManagement.css';

const gridVariants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.05 } }
};

const cardVariants = {
  hidden: { opacity: 0, y: 16 },
  show: { opacity: 1, y: 0, transition: { duration: 0.3, ease: 'easeOut' } }
};

function formatDate(dateString) {
  if (!dateString) return 'N/A';
  const date = new Date(dateString);
  return date.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

const StoryManagement = ({ onPlayStory }) => {
  const [stories, setStories] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState('all'); // all, saved, generated
  const [searchTerm, setSearchTerm] = useState('');
  const [page, setPage] = useState(1);
  const [itemsPerPage, setItemsPerPage] = useState(getItemsPerPage(window.innerWidth));
  const [showConfirmDialog, setShowConfirmDialog] = useState(false);
  const [storyToDelete, setStoryToDelete] = useState(null);
  const [playingStoryId, setPlayingStoryId] = useState(null);

  const [titleDrafts, setTitleDrafts] = useState({});
  const [savingId, setSavingId] = useState(null);

  useEffect(() => {
    fetchStories();
    const handleResize = () => setItemsPerPage(getItemsPerPage(window.innerWidth));
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  useEffect(() => {
    setPage(1);
  }, [searchTerm, filter]);

  const fetchStories = async () => {
    try {
      setLoading(true);
      const response = await apiClient.get('/api/admin/stories/all');
      if (response.data.success) {
        setStories(response.data.stories);
        setError(null);
      }
    } catch (err) {
      setError('Failed to fetch stories: ' + (err.response?.data?.detail || err.message));
      console.error('Fetch stories error:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = (story) => {
    setStoryToDelete(story);
    setShowConfirmDialog(true);
  };

  const confirmDelete = async () => {
    try {
      await apiClient.delete(`/api/admin/stories/${storyToDelete.story_id}`);
      await fetchStories();
      setShowConfirmDialog(false);
      setStoryToDelete(null);
    } catch (err) {
      setError('Failed to delete story: ' + (err.response?.data?.detail || err.message));
      console.error('Delete story error:', err);
    }
  };

  const handlePlay = async (story) => {
    setPlayingStoryId(story.story_id);
    try {
      await onPlayStory(story.story_id);
    } catch (err) {
      setError('Failed to play story: ' + (err.response?.data?.detail || err.message));
      console.error('Play story error:', err);
    } finally {
      setPlayingStoryId(null);
    }
  };

  const getTitle = (story) => story.title || story.name || 'Untitled';
  const getDraft = (story) => titleDrafts[story.story_id] ?? getTitle(story);

  // Mirrors STORY_MIN_COVERAGE / STORY_MIN_HALLUCINATION_SCORE /
  // STORY_MIN_FAITHFULNESS / STORY_MIN_CITATION_ACCURACY in
  // backend/services/story_service.py - keep in sync if those change.
  const QUALITY_GATES = [
    { key: 'coverage', label: 'Coverage', min: 100 },
    { key: 'hallucination', label: 'Hallucination', min: 98 },
    { key: 'faithfulness', label: 'Faithfulness', min: 95 },
    { key: 'citation_accuracy', label: 'Citation', min: 95 },
  ];

  const gateStatus = (scores) =>
    QUALITY_GATES.map((g) => ({
      ...g,
      value: scores[g.key],
      pass: typeof scores[g.key] === 'number' && scores[g.key] >= g.min,
    }));

  const handleRename = async (story) => {
    const newTitle = getDraft(story).trim();
    if (!newTitle || newTitle === getTitle(story)) return;

    setSavingId(story.story_id);
    setError(null);
    try {
      await apiClient.put(`/api/admin/stories/${story.story_id}`, { title: newTitle });
      setTitleDrafts((prev) => { const next = { ...prev }; delete next[story.story_id]; return next; });
      await fetchStories();
    } catch (err) {
      setError('Rename failed: ' + (err.response?.data?.detail || err.message));
    } finally {
      setSavingId(null);
    }
  };

  const filteredStories = stories.filter(story => {
    if (filter === 'saved' && story.story_type !== 'saved') return false;
    if (filter === 'generated' && story.story_type !== 'generated') return false;

    if (searchTerm) {
      const term = searchTerm.toLowerCase();
      return (
        story.title?.toLowerCase().includes(term) ||
        story.name?.toLowerCase().includes(term) ||
        story.username?.toLowerCase().includes(term) ||
        story.email?.toLowerCase().includes(term) ||
        story.story_id?.toLowerCase().includes(term)
      );
    }

    return true;
  });

  const savedCount = stories.filter(s => s.story_type === 'saved').length;
  const generatedCount = stories.filter(s => s.story_type === 'generated').length;

  const totalPages = Math.max(1, Math.ceil(filteredStories.length / itemsPerPage));
  const startIndex = (page - 1) * itemsPerPage;
  const paginatedStories = filteredStories.slice(startIndex, startIndex + itemsPerPage);

  if (loading) {
    return <div className="loading">Loading stories...</div>;
  }

  return (
    <div className="story-management">
      <div className="management-header">
        <h2>Story Management</h2>
        <div className="header-actions">
          <button className="refresh-btn" onClick={fetchStories}>Refresh</button>
        </div>
      </div>

      {error && <div className="error-message">{error}</div>}

      <div className="filters-section">
        <div className="filter-tabs">
          <button className={`filter-tab ${filter === 'all' ? 'active' : ''}`} onClick={() => setFilter('all')}>
            All Stories ({stories.length})
          </button>
          <button className={`filter-tab ${filter === 'saved' ? 'active' : ''}`} onClick={() => setFilter('saved')}>
            Saved ({savedCount})
          </button>
          <button className={`filter-tab ${filter === 'generated' ? 'active' : ''}`} onClick={() => setFilter('generated')}>
            Generated ({generatedCount})
          </button>
        </div>

        <div className="search-container">
          <Search size={18} className="search-icon" />
          <input
            type="text"
            className="search-input"
            placeholder="Search by title, user, or ID..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
          />
          {searchTerm && (
            <button className="clear-search" onClick={() => setSearchTerm('')} title="Clear search">
              <X size={16} />
            </button>
          )}
        </div>
      </div>

      {filteredStories.length > 0 && (
        <div className="results-info">
          Showing {startIndex + 1}-{Math.min(startIndex + itemsPerPage, filteredStories.length)} of {filteredStories.length} {filteredStories.length === 1 ? 'story' : 'stories'}
        </div>
      )}

      {filteredStories.length === 0 ? (
        <div className="no-results">No stories found{searchTerm && ` matching "${searchTerm}"`}</div>
      ) : (
        <motion.div className="stories-grid" variants={gridVariants} initial="hidden" animate="show">
          {paginatedStories.map((story) => (
            <motion.div key={story.story_id} variants={cardVariants}>
              <Flip3DCard
                frontLabel={`View full details for ${getTitle(story)}`}
                backLabel="Back to summary"
                front={
                  <div className="story-card-front">
                    <div className="story-card-badges">
                      <span className={`type-badge ${story.story_type}`}>{story.story_type}</span>
                      {story.status && <span className={`status-badge ${story.status}`}>{story.status}</span>}
                    </div>
                    <h3>{getTitle(story)}</h3>
                    <p className="story-card-owner"><User size={13} /> {story.username || 'Unknown'}</p>
                    <div className="story-card-stats">
                      <span><Calendar size={13} /> {formatDate(story.created_at)}</span>
                      {story.story_type === 'generated' && (
                        <span><Layers size={13} /> {story.completed_scenes ?? 0}/{story.total_scenes ?? 0} scenes</span>
                      )}
                    </div>
                  </div>
                }
                back={
                  <div className="story-card-back">
                    <h4>Story Details</h4>
                    <p className="story-id-line"><Hash size={12} /> {story.story_id}</p>
                    {story.email && <p className="story-detail-line"><Mail size={12} /> {story.email}</p>}
                    <p className="story-detail-line"><Calendar size={12} /> Created {formatDate(story.created_at)}</p>
                    {story.updated_at && <p className="story-detail-line"><Calendar size={12} /> Updated {formatDate(story.updated_at)}</p>}

                    {story.quality_scores ? (
                      <div className="quality-scores">
                        <div className="quality-scores-header">
                          <Gauge size={13} />
                          <span>
                            Quality: {story.quality_scores.gates_passed ?? '?'}/4 gates
                            {story.quality_scores.attempt ? ` (attempt ${story.quality_scores.attempt}/3)` : ''}
                          </span>
                        </div>
                        <div className="quality-gate-grid">
                          {gateStatus(story.quality_scores).map((g) => (
                            <span key={g.key} className={`quality-gate-badge ${g.pass ? 'pass' : 'fail'}`}>
                              {g.pass ? <CheckCircle2 size={12} /> : <XCircle size={12} />}
                              {g.label} {typeof g.value === 'number' ? Math.round(g.value) : '?'}%
                            </span>
                          ))}
                        </div>
                        <p className="quality-overall">Overall {Math.round(story.quality_scores.overall ?? 0)}%</p>
                        {story.quality_scores.missing_items?.length > 0 && (
                          <p className="quality-findings">
                            <strong>Missing:</strong> {story.quality_scores.missing_items.slice(0, 5).join('; ')}
                            {story.quality_scores.missing_items.length > 5 && ` (+${story.quality_scores.missing_items.length - 5} more)`}
                          </p>
                        )}
                        {story.quality_scores.unsupported_claims?.length > 0 && (
                          <p className="quality-findings quality-findings-warn">
                            <strong>Unsupported claims:</strong> {story.quality_scores.unsupported_claims.slice(0, 3).join('; ')}
                            {story.quality_scores.unsupported_claims.length > 3 && ` (+${story.quality_scores.unsupported_claims.length - 3} more)`}
                          </p>
                        )}
                      </div>
                    ) : (
                      story.story_type === 'generated' && (
                        <p className="story-detail-line quality-scores-missing">
                          <Gauge size={12} /> No quality scores recorded
                        </p>
                      )
                    )}

                    {story.api_usage ? (
                      <div className="api-usage">
                        <div className="api-usage-header">
                          <Activity size={13} />
                          <span>
                            API calls: {story.api_usage.total}
                            {story.api_usage.errors > 0 && (
                              <span className="api-usage-errors"> ({story.api_usage.errors} failed)</span>
                            )}
                          </span>
                        </div>
                        <div className="api-usage-grid">
                          {story.api_usage.breakdown.map((b) => (
                            <span key={`${b.provider}-${b.model}`} className="api-usage-badge">
                              <span className="api-usage-provider">{b.provider}</span>
                              <span className="api-usage-model">{b.model}</span>
                              <span className="api-usage-count">{b.calls}</span>
                            </span>
                          ))}
                        </div>
                      </div>
                    ) : (
                      /* Distinct from "0 calls": counting started on 2026-08-06,
                         so an older story has no row rather than an empty one. */
                      story.story_type === 'generated' && (
                        <p className="story-detail-line quality-scores-missing">
                          <Activity size={12} /> API calls not recorded
                        </p>
                      )
                    )}

                    <label className="field-label">
                      <span className="field-label-text"><Pencil size={12} /> Title</span>
                      <input
                        type="text"
                        value={getDraft(story)}
                        onChange={(e) => setTitleDrafts((prev) => ({ ...prev, [story.story_id]: e.target.value }))}
                      />
                    </label>

                    <div className="story-card-actions">
                      <button
                        className="save-btn"
                        onClick={() => handleRename(story)}
                        disabled={savingId === story.story_id || getDraft(story).trim() === getTitle(story)}
                      >
                        <Save size={14} /> {savingId === story.story_id ? 'Saving...' : 'Save'}
                      </button>
                      <button
                        className="story-play-btn"
                        onClick={() => handlePlay(story)}
                        disabled={playingStoryId === story.story_id}
                      >
                        <Play size={14} /> {playingStoryId === story.story_id ? 'Loading...' : 'Play'}
                      </button>
                      <button className="delete-btn" onClick={() => handleDelete(story)}>
                        <Trash2 size={14} /> Delete
                      </button>
                    </div>
                  </div>
                }
              />
            </motion.div>
          ))}
        </motion.div>
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

      {showConfirmDialog && (
        <div className="confirm-dialog-overlay">
          <div className="confirm-dialog">
            <h3>Delete Story</h3>
            <p>
              Are you sure you want to delete <strong>"{getTitle(storyToDelete)}"</strong>?
              <br />
              <br />
              This will permanently remove the story from the database and delete all associated files. This action cannot be undone.
            </p>
            <div className="dialog-actions">
              <button className="cancel-btn" onClick={() => { setShowConfirmDialog(false); setStoryToDelete(null); }}>
                Cancel
              </button>
              <button className="confirm-btn" onClick={confirmDelete}>
                Delete Story
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default StoryManagement;
