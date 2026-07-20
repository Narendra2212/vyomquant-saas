# Multi-stage build for optimized production image

# =============================================================================
# Stage 1: Builder
# =============================================================================
FROM python:3.11-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies
# Use backend_app/requirements.txt — canonical backend requirements
COPY backend_app/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir gunicorn httpx redis && \
    pip install --no-cache-dir -r requirements.txt

# =============================================================================
# Stage 2: Production
# =============================================================================
FROM python:3.11-slim as production

WORKDIR /app

# Create non-root user for security
RUN groupadd -r appuser && useradd -m -r -g appuser appuser

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy canonical backend package
# backend_app/ is the ONLY backend — aerora_quant_backend_updated_final1/ is excluded
COPY --chown=appuser:appuser backend_app/ ./backend_app/

# Copy startup scripts
COPY --chown=appuser:appuser startup.sh healthcheck.sh ./
RUN chmod +x startup.sh healthcheck.sh

# Create logs directory
RUN mkdir -p /app/logs && chown -R appuser:appuser /app/logs

# Switch to non-root user
USER appuser

# Health check uses the provided script
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD ./healthcheck.sh

# Expose port
EXPOSE 8000

# Set up logging and unbuffered output
ENV PYTHONUNBUFFERED=1
ENV LOG_LEVEL=INFO
ENV LOG_FORMAT=json
# PYTHONPATH ensures backend_app package is importable from /app
ENV PYTHONPATH=/app

# Run application using the startup script
CMD ["./startup.sh"]
