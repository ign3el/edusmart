import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { ChevronLeft } from 'lucide-react';
import './AdminPanel.css';
import JobStatusViewer from './JobStatusViewer';
import UserManagement from './UserManagement';
import StoryManagement from './StoryManagement';
import PlanManagement from './PlanManagement';
import PromoCodeManagement from './PromoCodeManagement';
import SystemDashboard from './SystemDashboard';
import FeatureFlags from './FeatureFlags';
import AuditLog from './AuditLog';
import ContentReview from './ContentReview';

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
                        className={`admin-nav-button ${activeTab === 'plans' ? 'active' : ''}`}
                        onClick={() => setActiveTab('plans')}
                    >
                        Plans
                    </button>
                    <button
                        className={`admin-nav-button ${activeTab === 'promo-codes' ? 'active' : ''}`}
                        onClick={() => setActiveTab('promo-codes')}
                    >
                        Promo Codes
                    </button>
                    <button
                        className={`admin-nav-button ${activeTab === 'jobs' ? 'active' : ''}`}
                        onClick={() => setActiveTab('jobs')}
                    >
                        Job Status
                    </button>
                    <button
                        className={`admin-nav-button ${activeTab === 'system' ? 'active' : ''}`}
                        onClick={() => setActiveTab('system')}
                    >
                        System
                    </button>
                    <button
                        className={`admin-nav-button ${activeTab === 'feature-flags' ? 'active' : ''}`}
                        onClick={() => setActiveTab('feature-flags')}
                    >
                        Feature Flags
                    </button>
                    <button
                        className={`admin-nav-button ${activeTab === 'content-review' ? 'active' : ''}`}
                        onClick={() => setActiveTab('content-review')}
                    >
                        Content Review
                    </button>
                    <button
                        className={`admin-nav-button ${activeTab === 'audit-log' ? 'active' : ''}`}
                        onClick={() => setActiveTab('audit-log')}
                    >
                        Audit Log
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
                        {activeTab === 'plans' && <PlanManagement />}
                        {activeTab === 'promo-codes' && <PromoCodeManagement />}
                        {activeTab === 'jobs' && <JobStatusViewer />}
                        {activeTab === 'system' && <SystemDashboard />}
                        {activeTab === 'feature-flags' && <FeatureFlags />}
                        {activeTab === 'content-review' && <ContentReview />}
                        {activeTab === 'audit-log' && <AuditLog />}
                    </motion.div>
                </AnimatePresence>
            </main>
        </div>
    );
};

export default AdminPanel;
