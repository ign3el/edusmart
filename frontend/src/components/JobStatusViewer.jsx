import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { motion } from 'framer-motion';
import {
  Search, X, CheckCircle2, Loader2, XCircle, Circle, Calendar, Hash,
  Image as ImageIcon, Volume2, ChevronLeft, ChevronRight, Ban, RefreshCw, Trash2
} from 'lucide-react';
import { getJobStateTableData, cancelStuckJob, retryFailedJob, deleteJob } from '../services/api';
import { getItemsPerPage } from '../utils/responsiveUtils';
import Flip3DCard from './admin/Flip3DCard';
import './JobStatusViewer.css';

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
  return new Date(dateString).toLocaleString();
}

const SceneStatusIcon = ({ status }) => {
  let icon, text;
  switch (status) {
    case 'completed':
      icon = <CheckCircle2 size={15} color="#22c55e" />;
      text = 'Completed';
      break;
    case 'processing':
      icon = <Loader2 size={15} color="#eab308" className="spin-icon" />;
      text = 'Processing';
      break;
    case 'failed':
      icon = <XCircle size={15} color="#ef4444" />;
      text = 'Failed';
      break;
    default:
      icon = <Circle size={15} color="#6b7280" />;
      text = 'Pending';
  }
  return <span title={text} className="scene-status-icon">{icon}</span>;
};

const JobStatusViewer = () => {
  const [jobs, setJobs] = useState([]);
  const [scenes, setScenes] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState('');
  const [searchTerm, setSearchTerm] = useState('');
  const [page, setPage] = useState(1);
  const [itemsPerPage, setItemsPerPage] = useState(getItemsPerPage(window.innerWidth));
  const [cancellingId, setCancellingId] = useState(null);
  const [retryingId, setRetryingId] = useState(null);
  const [deletingId, setDeletingId] = useState(null);
  const [notice, setNotice] = useState('');

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    setError('');
    try {
      const [jobsResponse, scenesResponse] = await Promise.all([
        getJobStateTableData('stories'),
        getJobStateTableData('scenes')
      ]);
      setJobs(jobsResponse.content || []);
      setScenes(scenesResponse.content || []);
    } catch (err) {
      setError('Failed to load job status data.');
      console.error(err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();

    const handleResize = () => setItemsPerPage(getItemsPerPage(window.innerWidth));
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [fetchData]);

  const handleCancel = async (storyId) => {
    if (!window.confirm('Mark this job as failed and refund the credit?')) return;
    setCancellingId(storyId);
    try {
      await cancelStuckJob(storyId);
      await fetchData();
    } catch (err) {
      setError('Failed to cancel job: ' + (err.response?.data?.detail || err.message));
    } finally {
      setCancellingId(null);
    }
  };

  // A failed job is only worth retrying if it actually has recoverable scenes.
  // A job that died before any scene rows were written has nothing to rebuild
  // from - the uploaded document is gone - so it can only be deleted.
  const retryableCount = (storyId) =>
    (scenesByJobId[storyId] || []).filter(
      (s) => s.image_status !== 'completed' || s.audio_status !== 'completed'
    ).length;

  const handleRetry = async (storyId) => {
    setRetryingId(storyId);
    setError('');
    setNotice('');
    try {
      const result = await retryFailedJob(storyId);
      setNotice(result.message || 'Retry started.');
      await fetchData();
    } catch (err) {
      setError('Retry failed: ' + (err.response?.data?.detail || err.message));
    } finally {
      setRetryingId(null);
    }
  };

  const handleDelete = async (storyId) => {
    if (!window.confirm('Permanently delete this job and all of its files? This cannot be undone.')) return;
    setDeletingId(storyId);
    setError('');
    setNotice('');
    try {
      await deleteJob(storyId);
      setNotice('Job deleted.');
      await fetchData();
    } catch (err) {
      setError('Delete failed: ' + (err.response?.data?.detail || err.message));
    } finally {
      setDeletingId(null);
    }
  };

  useEffect(() => {
    setPage(1);
  }, [searchTerm]);

  const scenesByJobId = useMemo(() => {
    return scenes.reduce((acc, scene) => {
      (acc[scene.story_id] = acc[scene.story_id] || []).push(scene);
      return acc;
    }, {});
  }, [scenes]);

  const filteredJobs = jobs.filter(job => {
    if (!searchTerm) return true;
    const term = searchTerm.toLowerCase();
    return job.title?.toLowerCase().includes(term) || job.story_id?.toLowerCase().includes(term);
  });

  const totalPages = Math.max(1, Math.ceil(filteredJobs.length / itemsPerPage));
  const startIndex = (page - 1) * itemsPerPage;
  const paginatedJobs = filteredJobs.slice(startIndex, startIndex + itemsPerPage);

  if (isLoading) {
    return <div className="loading-message">Loading job statuses...</div>;
  }

  if (error && jobs.length === 0) {
    return <div className="error-message">{error}</div>;
  }

  return (
    <div className="job-status-viewer">
      {error && jobs.length > 0 && <div className="error-message inline">{error}</div>}
      {notice && <div className="job-notice">{notice}</div>}
      <div className="search-container">
        <Search size={18} className="search-icon" />
        <input
          type="text"
          className="search-input"
          placeholder="Search by title or story ID..."
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
        />
        {searchTerm && (
          <button className="clear-search" onClick={() => setSearchTerm('')} title="Clear search">
            <X size={16} />
          </button>
        )}
      </div>

      {filteredJobs.length > 0 && (
        <div className="results-info">
          Showing {startIndex + 1}-{Math.min(startIndex + itemsPerPage, filteredJobs.length)} of {filteredJobs.length} {filteredJobs.length === 1 ? 'job' : 'jobs'}
        </div>
      )}

      {filteredJobs.length === 0 ? (
        <div className="no-results">No jobs found{searchTerm && ` matching "${searchTerm}"`}</div>
      ) : (
        <motion.div className="jobs-grid" variants={gridVariants} initial="hidden" animate="show">
          {paginatedJobs.map((job) => {
            const jobScenes = scenesByJobId[job.story_id] || [];
            const progress = job.total_scenes > 0 ? (job.completed_scenes / job.total_scenes) * 100 : 0;
            return (
              <motion.div key={job.story_id} variants={cardVariants}>
                <Flip3DCard
                  frontLabel={`View scene breakdown for ${job.title}`}
                  backLabel="Back to summary"
                  front={
                    <div className="job-card-front">
                      <span className={`status-pill status-${job.status}`}>{job.status}</span>
                      <h3>{job.title}</h3>
                      <div className="job-progress-bar-container">
                        <div className="job-progress-bar-inner" style={{ width: `${progress}%` }}></div>
                      </div>
                      <p className="job-progress-text">{job.completed_scenes} / {job.total_scenes} scenes</p>
                      <p className="job-card-date"><Calendar size={13} /> {formatDate(job.created_at)}</p>
                      {job.status === 'processing' && (
                        <button
                          type="button"
                          className="job-cancel-btn"
                          onClick={(e) => { e.stopPropagation(); handleCancel(job.story_id); }}
                          disabled={cancellingId === job.story_id}
                        >
                          <Ban size={13} /> {cancellingId === job.story_id ? 'Cancelling...' : 'Cancel stuck job'}
                        </button>
                      )}
                      {job.status === 'failed' && (
                        <div className="job-actions">
                          {retryableCount(job.story_id) > 0 && (
                            <button
                              type="button"
                              className="job-retry-btn"
                              onClick={(e) => { e.stopPropagation(); handleRetry(job.story_id); }}
                              disabled={retryingId === job.story_id}
                              title="Regenerate only the images/audio that failed, reusing the stored scene text"
                            >
                              <RefreshCw size={13} />
                              {retryingId === job.story_id
                                ? 'Retrying...'
                                : `Retry ${retryableCount(job.story_id)} scene${retryableCount(job.story_id) === 1 ? '' : 's'}`}
                            </button>
                          )}
                          <button
                            type="button"
                            className="job-delete-btn"
                            onClick={(e) => { e.stopPropagation(); handleDelete(job.story_id); }}
                            disabled={deletingId === job.story_id}
                          >
                            <Trash2 size={13} /> {deletingId === job.story_id ? 'Deleting...' : 'Delete'}
                          </button>
                        </div>
                      )}
                    </div>
                  }
                  back={
                    <div className="job-card-back">
                      <h4>Scene Breakdown</h4>
                      <p className="job-id-line"><Hash size={12} /> {job.story_id}</p>
                      {jobScenes.length === 0 ? (
                        <p className="no-scenes">No scene data yet.</p>
                      ) : (
                        <ul className="scene-list">
                          {jobScenes.map(scene => (
                            <li key={scene.scene_id}>
                              <span className="scene-index">Scene {scene.scene_index + 1}</span>
                              <span className="scene-status">
                                <ImageIcon size={13} /> <SceneStatusIcon status={scene.image_status} />
                              </span>
                              <span className="scene-status">
                                <Volume2 size={13} /> <SceneStatusIcon status={scene.audio_status} />
                              </span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  }
                />
              </motion.div>
            );
          })}
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
    </div>
  );
};

export default JobStatusViewer;
