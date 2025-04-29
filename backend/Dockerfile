FROM mcr.microsoft.com/azure-functions/python:4-python3.10

# Install system dependencies for geospatial libraries and build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgdal-dev \
    gdal-bin \
    libgeos-dev \
    libproj-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Set environment variables for GDAL and PROJ
ENV GDAL_DATA=/usr/share/gdal \
    PROJ_LIB=/usr/share/proj

# Set the working directory for Azure Functions
WORKDIR /home/site/wwwroot

# Copy and install dependencies first for Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Pre-compile Python files for faster cold starts (optional)
RUN find . -name "*.py" -print | xargs python -m compileall -q