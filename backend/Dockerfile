FROM python:3.11-slim

WORKDIR /app

# Build args to conditionally install heavy optional components
ARG INSTALL_OFFICE=false
ARG INSTALL_BROWSER=false

# Install system dependencies
# gcc/g++: compile native Python extensions (chromadb, etc.)
# poppler-utils: PDF page rendering for scanned PDF OCR (pdf2image fallback)
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    poppler-utils \
    && if [ "$INSTALL_OFFICE" = "true" ]; then \
        apt-get install -y --no-install-recommends libreoffice; \
    fi \
    && if [ "$INSTALL_BROWSER" = "true" ]; then \
        pip install --no-cache-dir playwright && \
        playwright install chromium && \
        playwright install-deps chromium; \
    fi \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Keep a copy of bundled templates outside the /app/data volume to prevent volume masking
RUN cp -r /app/data/personality /app/bundled_personality && cp -r /app/data/personas /app/bundled_personas

# Create data directories
RUN mkdir -p /app/data/db /app/data/personality /app/data/projects /app/data/workspace /app/data/chroma

# Copy default personality files if they exist
RUN cp -n /app/data/personality/soul.md /app/data/personality/soul.md 2>/dev/null || true

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
