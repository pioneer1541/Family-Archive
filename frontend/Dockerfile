# Family Vault Frontend - Next.js
# Multi-stage build for production

# Stage 1: Dependencies
FROM node:20-alpine AS deps
WORKDIR /app

# Install dependencies for Playwright (optional, for e2e)
RUN apk add --no-cache libc6-compat

COPY package.json package-lock.json* ./
RUN npm ci

# Stage 2: Builder
FROM node:20-alpine AS builder
WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# Build arguments for environment variables
ARG NEXT_PUBLIC_FKV_API_BASE=/api
ENV NEXT_PUBLIC_FKV_API_BASE=$NEXT_PUBLIC_FKV_API_BASE
ENV NEXT_TELEMETRY_DISABLED=1
ENV NEXT_DIST_DIR=.next-runtime

RUN npm run build

# Stage 3: Runner
FROM node:20-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV NEXT_DIST_DIR=.next-runtime

# Create non-root user
RUN addgroup --system --gid 1001 nodejs
RUN adduser --system --uid 1001 nextjs

# Copy built application
COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next-runtime ./next-runtime
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./package.json

# Set correct ownership
RUN chown -R nextjs:nodejs /app

USER nextjs

EXPOSE 18081

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3     CMD wget --no-verbose --tries=1 --spider http://localhost:18081/ || exit 1

CMD ["npm", "start"]
