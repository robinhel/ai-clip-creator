FROM node:22-slim AS builder
WORKDIR /app
# corepack uses the pnpm version in package.json ("packageManager").
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
# The Cypress test browser is not needed in the image: its install script skips the download.
ENV CYPRESS_INSTALL_BINARY=0
RUN pnpm install --frozen-lockfile
COPY . .
ARG NEXT_PUBLIC_API_BASE=http://localhost:8000
RUN NEXT_PUBLIC_API_BASE=${NEXT_PUBLIC_API_BASE} pnpm run build
# Remove auto-generated proxy middleware that hard-codes NEXT_PUBLIC_API_BASE at build time.
# Without this, Next.js intercepts /api/* before our route.ts handler can run.
RUN node -e "const fs=require('fs'); \
    ['.next/server/functions-config-manifest.json', \
     '.next/standalone/.next/server/functions-config-manifest.json'].forEach(p => { \
      if (!fs.existsSync(p)) return; \
      const m=JSON.parse(fs.readFileSync(p,'utf8')); \
      delete m.functions['/_middleware']; \
      fs.writeFileSync(p,JSON.stringify(m,null,2)); \
      console.log('Patched', p); \
    });"

FROM node:22-slim AS runner
WORKDIR /app
ENV NODE_ENV=production
# Listen on every address inside the container; Compose publishes the port on 127.0.0.1 only.
ENV HOSTNAME=0.0.0.0
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
