#!/bin/bash

# Exit on any error
set -e

echo "Starting installation of Python 3.11 and dependencies..."

# Update package lists
sudo apt-get update

# Install Python 3.11 from deadsnakes PPA
echo "Installing Python 3.11..."
sudo apt-get install -y software-properties-common
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt-get update
sudo apt-get install -y \
    python3.11 \
    python3.11-dev \
    python3.11-venv \
    python3-pip \
    python3-setuptools

# Make python3.11 the default python3
echo "Setting Python 3.11 as default python3..."
sudo update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1

# Install system dependencies required for Tesseract build
echo "Installing system dependencies for Tesseract..."
sudo apt-get install -y \
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
    unpaper \
    ghostscript


# Note: You'll need to provide the build_tesseract.sh script
echo "Building Tesseract with Arabic and Greek language support..."
chmod +x build_tesseract.sh
sudo cp build_tesseract.sh /usr/local/bin/
echo y | sudo /usr/local/bin/build_tesseract.sh -v 5.5.1 -l eng,ara,ell --jobs 32


# Upgrade pip and install package managers
echo "Installing Python package managers..."
python3 -m pip install --upgrade setuptools[core]
python3 -m pip install uv

echo "Installation completed successfully!"
echo "Note: If you have a requirements.txt or pyproject.toml file, you can now run:"
echo "uv sync --extra ui --extra cu124"