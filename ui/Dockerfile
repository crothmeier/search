FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Copy requirements file
COPY requirements-ui.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements-ui.txt

# Copy ui directory
COPY ui/ ./ui/

# Copy streamlit config
COPY .streamlit/config.toml .streamlit/config.toml

# Create non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8501

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

# Run streamlit
CMD ["streamlit", "run", "ui/app.py", "--server.fileWatcherType", "poll"]
