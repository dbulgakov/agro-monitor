# Use an official Azure Functions Python base image
FROM mcr.microsoft.com/azure-functions/python:4-python3.9

# Install system dependencies for geospatial libraries
# Using Ubuntu package manager (apt) based on the base image
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgdal-dev \
    gdal-bin \
    libgeos-dev \
    && rm -rf /var/lib/apt/lists/*

# Set environment variable for GDAL data
# This might vary depending on the exact installation paths in the base image/apt packages
# Check the image if needed: docker run --rm -it <image> find / -name gdal-data
ENV GDAL_DATA /usr/share/gdal 

# Set the working directory
WORKDIR /home/site/wwwroot

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
# Use --no-cache-dir to reduce image size
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Optional: Pre-compile Python files (can speed up cold starts slightly)
# RUN python -m compileall . 