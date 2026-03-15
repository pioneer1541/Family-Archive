FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies including cifs-utils for SMB mount
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        curl \
        cifs-utils \
        sudo \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user with sudo privileges for mount commands
RUN groupadd -r appgroup && useradd -r -g appgroup appuser \
    && echo "appuser ALL=(ALL) NOPASSWD: /bin/mount, /bin/umount, /usr/bin/mount, /usr/bin/umount" >> /etc/sudoers.d/appuser \
    && chmod 0440 /etc/sudoers.d/appuser

# Create mount base directory
RUN mkdir -p /mnt/nas_shares && chown -R appuser:appgroup /mnt/nas_shares

# Install Python dependencies
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy application code
COPY . /app

# Create data directory and set permissions
RUN mkdir -p /app/data \
    && chown -R appuser:appgroup /app

# Switch to non-root user
USER appuser

EXPOSE 18080

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:18080/v1/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "18080"]
