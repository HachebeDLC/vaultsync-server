FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for psycopg2 and cryptography
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the modular app code
COPY app/ ./app/

# Create storage directory and set permissions
RUN mkdir -p /app/storage && \
    useradd -m vaultsync && \
    chown -R vaultsync:vaultsync /app

USER vaultsync

EXPOSE 8000

# Run with uvicorn pointing to the modular app entry point
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
