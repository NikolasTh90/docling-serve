FROM python:3.12-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV DEBIAN_FRONTEND=noninteractive

# Set working directory
WORKDIR /app

# Install system dependencies required by build_tesseract.sh
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    git \
    pkg-config \
    libtool \
    autoconf \
    automake \
    libpng-dev \
    libjpeg-dev \
    libtiff-dev \
    libgif-dev \
    libwebp-dev \
    libopenjp2-7-dev \
    zlib1g-dev \
    liblcms2-dev \
    libicu-dev \
    libpango1.0-dev \
    libcairo2-dev \
    curl \
    wget \
    sudo \
    && rm -rf /var/lib/apt/lists/*

# Copy build script and make it executable
COPY build_tesseract.sh /usr/local/bin/build_tesseract.sh
RUN chmod +x /usr/local/bin/build_tesseract.sh

# Build and install Tesseract with Arabic and Greek language support
RUN echo y | /usr/local/bin/build_tesseract.sh -v 5.5.1 -l eng,ara,ell

# Copy project files
COPY pyproject.toml ./
COPY README.md ./

# Install UV package manager for faster dependency resolution
RUN pip install uv

# Install Python dependencies
# Install with UI support and tesserocr for better OCR integration
RUN uv pip install --system -e ".[ui,tesserocr,cpu]"

# Copy application source code
COPY docling_serve/ ./docling_serve/

# Create non-root user
RUN useradd --create-home --shell /bin/bash app && \
    chown -R app:app /app
USER app

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:5001/health || exit 1

# Expose port
EXPOSE 5001

# Default command
CMD ["docling-serve", "run", "--host", "0.0.0.0", "--port", "5001", "--enable-ui"]