# Opt-in candidate, NOT used by compose.yaml. Require an immutable project image
# (including fonts) built with onlyoffice.Dockerfile, passed as name@sha256:digest.
ARG ONLYOFFICE_BASE
FROM ${ONLYOFFICE_BASE}
ARG ONLYOFFICE_BASE
RUN case "$ONLYOFFICE_BASE" in *@sha256:*) ;; *) echo 'Digest-pinned base required' >&2; exit 1;; esac
COPY scripts/patch_onlyoffice_precision.py /opt/mai-precision/patch.py
COPY docker/onlyoffice-precision-entrypoint.sh /opt/mai-precision/entrypoint.sh
RUN python3 /opt/mai-precision/patch.py --install /var/www/onlyoffice/documentserver/sdkjs/slide \
    && chmod 755 /opt/mai-precision/entrypoint.sh
LABEL org.mai.onlyoffice.precision="candidate-1"
ENTRYPOINT ["/opt/mai-precision/entrypoint.sh"]