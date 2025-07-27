FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Environment variables for Docker
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_LOGGER_LEVEL=info

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements-ui.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements-ui.txt && \
    pip install --no-cache-dir pandas

# Create non-root user
RUN useradd -m -u 1000 streamlit && \
    chown -R streamlit:streamlit /app

# Copy application code
COPY --chown=streamlit:streamlit app.py .

# Copy Streamlit config
COPY --chown=streamlit:streamlit .streamlit /app/.streamlit

# Switch to non-root user
USER streamlit

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8501/_stcore/health').raise_for_status()"

# Expose port
EXPOSE 8501

# Run Streamlit
CMD ["streamlit", "run", "app.py", "--server.fileWatcherType=poll"]
EOF < /dev/null
