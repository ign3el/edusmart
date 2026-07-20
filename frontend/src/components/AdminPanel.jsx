import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { ChevronLeft } from 'lucide-react';
import './AdminPanel.css';
import JobStatusViewer from './JobStatusViewer';
import UserManagement from './UserManagement';
import StoryManagement from './StoryManagement';

const AdminPanel = ({ onPlayStory, onBack }) => {
    const [activeTab, setActiveTab] = useState('story-management');

    return (
        <div className="admin-panel">
            <header className="admin-panel-header">
                <div className="admin-panel-header-top">
                    <h1>Admin Panel</h1>
                    <button onClick={onBack} className="admin-back-button"><ChevronLeft size={14} /> Back to Home</button>
                </div>
                <nav className="admin-panel-nav">
                    <button 
                        className={`admin-nav-button ${activeTab === 'story-management' ? 'active' : ''}`}
                        onClick={() => setActiveTab('story-management')}
                    >
                        All Stories
                    </button>
                    <button 
                        className={`admin-nav-button ${activeTab === 'users' ? 'active' : ''}`}
                        onClick={() => setActiveTab('users')}
                    >
                        Users
                    </button>
                    <button 
                        className={`admin-nav-button ${activeTab === 'jobs' ? 'active' : ''}`}
                        onClick={() => setActiveTab('jobs')}
                    >
                        Job Status
                    </button>
                </nav>
            </header>
            <main className="admin-panel-content">
                <AnimatePresence mode="wait">
                    <motion.div
                        key={activeTab}
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        transition={{ duration: 0.15 }}
                    >
                        {activeTab === 'story-management' && <StoryManagement onPlayStory={onPlayStory} />}
                        {activeTab === 'users' && <UserManagement />}
                        {activeTab === 'jobs' && <JobStatusViewer />}
                    </motion.div>
                </AnimatePresence>
            </main>
        </div>
    );
};

export default AdminPanel;
