# Frontend Dockerfile
FROM node:20-alpine as build

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm install

# Copy source code
COPY . .

# Build-time configuration, passed from the compose-root .env. Vite picks
# VITE_* straight out of the environment, so no .env file needs to exist in
# the build context - the one that used to live here was excluded by a
# .dockerignore rule on 2026-07-25 and silently shipped a bundle pointing at
# localhost:8000, which took production login down.
ARG VITE_API_URL
ARG VITE_GOOGLE_CLIENT_ID
ARG VITE_FACEBOOK_APP_ID
ENV VITE_API_URL=$VITE_API_URL \
    VITE_GOOGLE_CLIENT_ID=$VITE_GOOGLE_CLIENT_ID \
    VITE_FACEBOOK_APP_ID=$VITE_FACEBOOK_APP_ID

# Fail loudly rather than shipping a broken bundle. An empty VITE_API_URL is
# not a build that needs a warning - it is a build that must not exist, because
# the failure only shows up later as "login does nothing" on a real phone.
RUN if [ -z "$VITE_API_URL" ]; then \
      echo "BUILD FAILED: VITE_API_URL is empty. Set APP_URL in the compose-root .env." >&2; \
      exit 1; \
    fi

# Build the application
RUN npm run build

# Production stage
FROM nginx:alpine

# Copy built assets from build stage
COPY --from=build /app/dist /usr/share/nginx/html

# Copy nginx configuration
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
