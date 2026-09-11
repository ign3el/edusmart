# Frontend Dockerfile
FROM node:20-alpine as build

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies. `npm ci`, not `npm install`: install treats the
# lockfile as advisory and may rewrite it, so two builds of one commit could
# ship different trees. ci fails loudly if package.json and the lock disagree.
RUN npm ci

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

# Pre-compress every text asset at MAX quality, once, at build time. This is
# strictly better than compressing per-request: brotli -q 11 is far too slow to
# run on the fly (seconds per MB) but costs nothing here, and the result is
# ~15-20% smaller than the on-the-fly gzip -5 the host nginx was applying.
# `-k` keeps the original so clients that send no Accept-Encoding still work.
# Images are skipped deliberately: png/jpg/webp are already compressed and
# brotli only makes them bigger.
RUN apk add --no-cache brotli gzip \
    && find /app/dist -type f \
       \( -name '*.js' -o -name '*.css' -o -name '*.html' -o -name '*.svg' \
          -o -name '*.json' -o -name '*.txt' -o -name '*.webmanifest' \) \
       -size +1k -print0 \
       | xargs -0 -r -n1 -P4 sh -c 'brotli -q 11 -k -f "$0" && gzip -9 -k -f "$0"'

# Production stage.
#
# Alpine's own nginx, NOT the official nginx:alpine image: brotli ships as
# `nginx-mod-http-brotli` in Alpine's community repo, and that package refuses
# to install alongside the official image's pinned nginx packages (they carry
# different nginx versions and conflict on nginx-module-*). Alpine's nginx is
# 1.26.x - the stable branch, versus 1.31 mainline - which for a static file
# server is the more conservative choice, not a downgrade in any sense we care
# about here. Host nginx keeps terminating TLS in front of this either way.
FROM alpine:3.20

# Three things happen below, and none of them are optional:
#   1. /etc/nginx/nginx.conf already does `include /etc/nginx/modules/*.conf`,
#      and the brotli package drops 10_http_brotli.conf there, so the two
#      load_module lines need no help from us.
#   2. Alpine ships a stock http.d/default.conf that would shadow ours.
#   3. The official nginx image symlinks its logs to the container's
#      stdout/stderr; Alpine's package writes to files, where `docker logs`
#      would never see them. Restore that or the next frontend problem is
#      invisible.
# (Comments live here rather than inside the RUN: a `#` line between
# backslash-continued lines is stripped by the Dockerfile parser before the
# shell ever sees it, which works but reads like a trap.)
RUN apk add --no-cache nginx nginx-mod-http-brotli \
    && rm -f /etc/nginx/http.d/default.conf \
    && ln -sf /dev/stdout /var/log/nginx/access.log \
    && ln -sf /dev/stderr /var/log/nginx/error.log

# Copy built assets from build stage (now including the .br/.gz siblings)
COPY --from=build /app/dist /usr/share/nginx/html

# Copy nginx configuration. Alpine's nginx includes conf.d OUTSIDE the http
# block and http.d INSIDE it - this file declares a `server`, so it must land
# in http.d or nginx refuses to start.
COPY nginx.conf /etc/nginx/http.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
