# syntax=docker/dockerfile:1

FROM node:22-slim AS builder
WORKDIR /app
ARG NEXT_PUBLIC_API_BASE=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:22-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production PORT=3000
# Standalone output = self-contained server + minimal node_modules.
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
RUN useradd --create-home --uid 10002 nextuser 2>/dev/null || true
USER nextuser
EXPOSE 3000
CMD ["node", "server.js"]
