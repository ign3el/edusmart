"""
Models Package

Data models for the LearnTale application using Pydantic for validation.

Models:
    - Scene: Individual scene with visual description, narration, and learning point
    - Screenplay: Collection of scenes forming the complete story
    - StoryRequest: API request model for story generation
    - StoryResponse: API response model with generated story data
    - AvatarType: Enum of available character types
"""

from .story import (
    StorySchema, 
    Scene, 
    QuizItem, 
    StoryRequest, 
    StoryResponse, 
    Screenplay
)

__all__ = ['StorySchema', 'Scene', 'QuizItem', 'StoryRequest', 'StoryResponse', 'Screenplay']