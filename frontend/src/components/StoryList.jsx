import React, { useState, useEffect, useMemo } from 'react';
import apiClient from '../services/api';
import './StoryList.css';

const StoryList = ({ onPlayStory }) => {
    const [stories, setStories] = useState([]);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState('');
    const [sortConfig, setSortConfig] = useState({ key: 'created_at', direction: 'descending' });
    const [filterUser, setFilterUser] = useState('');
    const [playingStoryId, setPlayingStoryId] = useState(null);

    useEffect(() => {
        const fetchAllStories = async () => {
            setIsLoading(true);
            setError('');
            try {
                // This API call should now work correctly for admins
                const response = await apiClient.get('/api/list-stories');
                setStories(response.data);
            } catch (err) {
                setError('Failed to load stories. Ensure you are logged in as an admin.');
                console.error(err);
            } finally {
                setIsLoading(false);
            }
        };

        fetchAllStories();
    }, []);

    const sortedAndFilteredStories = useMemo(() => {
        let sortableItems = [...stories];
        if (filterUser) {
            sortableItems = sortableItems.filter(story =>
                story.username.toLowerCase().includes(filterUser.toLowerCase()) ||
                story.name.toLowerCase().includes(filterUser.toLowerCase())
            );
        }
        if (sortConfig.key !== null) {
            sortableItems.sort((a, b) => {
                if (a[sortConfig.key] < b[sortConfig.key]) {
                    return sortConfig.direction === 'ascending' ? -1 : 1;
                }
                if (a[sortConfig.key] > b[sortConfig.key]) {
                    return sortConfig.direction === 'ascending' ? 1 : -1;
                }
                return 0;
            });
        }
        return sortableItems;
    }, [stories, sortConfig, filterUser]);

    const requestSort = (key) => {
        let direction = 'ascending';
        if (sortConfig.key === key && sortConfig.direction === 'ascending') {
            direction = 'descending';
        }
        setSortConfig({ key, direction });
    };

    const getSortClassName = (name) => {
        if (sortConfig.key !== name) {
          return '';
        }
        return sortConfig.direction === 'ascending' ? 'sorted-asc' : 'sorted-desc';
    };


    if (isLoading) {
        return <div className="loading-message">Loading all stories...</div>;
    }

    if (error) {
        return <div className="error-message">{error}</div>;
    }

    return (
        <div className="story-list">
            <div className="story-list-controls">
                <input
                    type="text"
                    id="filter-username"
                    name="filter-username"
                    placeholder="Filter by username or story name..."
                    value={filterUser}
                    onChange={(e) => setFilterUser(e.target.value)}
                    className="filter-input"
                />
            </div>
            <div className="story-table-container">
                <table>
                    <thead>
                        <tr>
                            <th onClick={() => requestSort('name')} className={getSortClassName('name')}>Story Name</th>
                            <th onClick={() => requestSort('username')} className={getSortClassName('username')}>Created By</th>
                            <th onClick={() => requestSort('created_at')} className={getSortClassName('created_at')}>Date Created</th>
                            <th>Story ID</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        {sortedAndFilteredStories.length === 0 ? (
                            <tr>
                                <td colSpan="5">No stories match your criteria.</td>
                            </tr>
                        ) : (
                            sortedAndFilteredStories.map(story => (
                                <tr key={story.story_id || story.id}>
                                    <td>{story.name}</td>
                                    <td>{story.username || 'N/A'}</td>
                                    <td>{new Date(story.created_at).toLocaleString()}</td>
                                    <td className="story-id-cell">{story.story_id}</td>
                                    <td>
                                        <button 
                                            className="play-story-button"
                                            disabled={playingStoryId === story.story_id}
                                            onClick={async () => {
                                                setPlayingStoryId(story.story_id);
                                                try {
                                                    await onPlayStory(story.story_id);
                                                } catch (err) {
                                                    console.error('Failed to play story:', err);
                                                } finally {
                                                    setPlayingStoryId(null);
                                                }
                                            }}
                                        >
                                            {playingStoryId === story.story_id ? 'Loading...' : 'Play'}
                                        </button>
                                    </td>
                                </tr>
                            ))
                        )}
                    </tbody>
                </table>
            </div>
        </div>
    );
};

export default StoryList;
