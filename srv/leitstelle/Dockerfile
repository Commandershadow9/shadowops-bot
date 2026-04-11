# Stage 1: Build
FROM node:22-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    make \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy package files and install dependencies
COPY package*.json ./
RUN npm ci

# Copy the rest of the application code
COPY . .

# Build the application if needed (e.g., TypeScript or Babel)
# Standard Node.js bots might not have a build script, so we use a conditional run
RUN if npm run build --if-present; then echo "Build successful"; else echo "No build script, skipping"; fi

# Stage 2: Runtime
FROM node:22-slim AS runner

WORKDIR /app

# Set production environment
ENV NODE_ENV=production

# Create a non-root user for security
RUN groupadd -g 1001 botuser && \
    useradd -u 1001 -g botuser -s /bin/sh -m botuser

# Copy necessary files from the build stage
COPY --from=builder --chown=botuser:botuser /app ./

# Switch to the non-root user
USER botuser

# Expose the application port (assuming the default from the issue)
EXPOSE 3200

# Start the application
# We use 'npm start' to handle start scripts in package.json
CMD ["npm", "start"]
