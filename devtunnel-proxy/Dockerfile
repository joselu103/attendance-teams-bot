FROM nginx:1.29-alpine

COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY docker-entrypoint.d/10-validate-upstream.sh /docker-entrypoint.d/10-validate-upstream.sh

RUN chmod 0555 /docker-entrypoint.d/10-validate-upstream.sh

EXPOSE 8080
