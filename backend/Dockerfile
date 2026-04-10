FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
# gcc/g++: compile native Python extensions (chromadb, etc.)
# poppler-utils: PDF page rendering for scanned PDF OCR (pdf2image fallback)
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create data directories
RUN mkdir -p /app/data/db /app/data/personality /app/data/projects /app/data/workspace /app/data/chroma

# Copy default personality files if they exist
RUN cp -n /app/data/personality/soul.md /app/data/personality/soul.md 2>/dev/null || true

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
