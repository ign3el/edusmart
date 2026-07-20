import React, { useState, useEffect, useMemo } from 'react';
import { motion } from 'framer-motion';
import {
  Search, X, CheckCircle2, Loader2, XCircle, Circle, Calendar, Hash,
  Image as ImageIcon, Volume2, ChevronLeft, ChevronRight
} from 'lucide-react';
import { getJobStateTableData } from '../services/api';
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

  useEffect(() => {
    const fetchData = async () => {
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
    };

    fetchData();

    const handleResize = () => setItemsPerPage(getItemsPerPage(window.innerWidth));
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

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

  if (error) {
    return <div className="error-message">{error}</div>;
  }

  return (
    <div className="job-status-viewer">
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
