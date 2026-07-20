# EduSmart

## Project Overview
Interactive educational storybook platform that generates AI-powered stories from educational materials with voice narration and visual illustrations.

## Tech Stack
- **Frontend**: React 18 + Vite + TypeScript
- **Backend**: FastAPI (Python)
- **Database**: PostgreSQL (via SQLAlchemy)
- **AI Integration**: Google Gemini + Groq for story generation
- **3D/Animation**: Three.js, React Three Fiber, Framer Motion
- **Voice**: Chatterbox TTS service
- **File Processing**: python-docx, python-pptx, PyPDF

## Key Features
- AI-powered story generation from uploaded documents
- Multi-provider AI support (Google Gemini, Groq)
- Voice narration using Chatterbox TTS
- Interactive 3D storybook interface with animations
- User authentication and progress tracking
- Email notifications for story completion
- Responsive design for mobile and desktop
- ZIP export of stories with assets

## Deployment
- **Method**: Docker Compose (multi-container)
- **Containers**:
  - Frontend: Vite dev server / static build
  - Backend: FastAPI with Uvicorn
  - Database: PostgreSQL
  - TTS: Chatterbox voice service

## Environment
- Google Gemini API key required
- Groq API key for alternative AI provider
- PostgreSQL connection configuration
- Chatterbox TTS service endpoint
- Email service for notifications (SMTP)
- JWT-based authentication

## Dependencies

### Frontend
- react: ^18.3.1
- react-dom: ^18.3.1
- react-router-dom: ^7.1.3
- @react-three/fiber: ^8.18.0
- @react-three/drei: ^9.122.0
- three: ^0.185.1
- framer-motion: ^11.15.0
- axios: ^1.7.9
- jszip: ^3.10.1
- react-dropzone: ^14.3.5
- react-icons: ^5.4.0
- vite: ^5.4.11

### Backend
- fastapi: 0.108.0
- uvicorn: >=0.30.0
- google-genai: >=0.2.0
- groq: >=0.4.0
- mysql-connector-python: >=9.5.0
- PyJWT: >=2.10.0
- python-jose[cryptography]: >=3.5.0
- passlib: >=1.7.4
- bcrypt: >=4.0.0
- fastapi-mail: >=1.6.1
- python-docx: 1.1.0
- python-pptx: 0.6.23
- pypdf: >=4.0.0
- aiohttp: >=3.9.0

## Status
Active - Main platform with full story generation and interactive features
