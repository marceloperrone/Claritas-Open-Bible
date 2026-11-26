# Serves the reader as plain files with Caddy.
# Only the page and its data go into the image. The Python scripts stay in the
# repo: they build chapters on your computer, not on the server.
FROM caddy:2-alpine

COPY Caddyfile /etc/caddy/Caddyfile
COPY index.html chapters.json glossary.json /srv/
COPY chapters/ /srv/chapters/
