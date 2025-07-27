FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first (better caching)
COPY requirements-ui.txt .
RUN pip install --no-cache-dir -r requirements-ui.txt

# Copy UI application
COPY ui/ ./ui/

# Create .streamlit directory
RUN mkdir -p /app/.streamlit

# Create non-root user
RUN useradd -m -u 1000 streamlit && chown -R streamlit:streamlit /app

# Set Python env vars
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

USER streamlit

WORKDIR /app/ui

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8501/ || exit 1

CMD ["streamlit", "run", "app.py", "--server.fileWatcherType=poll"]
