FROM node:24.22.2-alpine AS build

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ARG VITE_SITE_URL=""
ENV VITE_SITE_URL=$VITE_SITE_URL
RUN npm run build

FROM nginx:1.30.6-alpine

COPY deploy/nginx.conf /etc/nginx/nginx.conf
COPY --from=build /app/frontend/dist/ /usr/share/nginx/html/

USER nginx
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
