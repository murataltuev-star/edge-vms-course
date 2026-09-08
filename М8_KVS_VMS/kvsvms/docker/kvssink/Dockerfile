# docker/kvssink/Dockerfile — Lesson 8, Step 6. A genuine 20–40 minute build.
# Two departures from AWS's own sample Dockerfile are load-bearing here --
# see the annotations below before "simplifying" either one back.
FROM ubuntu:22.04
# NOT amazonlinux:2. That image's GCC (7.3.1) OOM-kills the compiler (cc1plus)
# partway through building one of the dependencies below, even with plenty of
# host memory free. Ubuntu 22.04's newer GCC doesn't.
RUN apt-get update && apt-get install -y \
    build-essential cmake git pkg-config \
    libssl-dev liblog4cplus-dev libcurl4-openssl-dev \
    gstreamer1.0-tools gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
    libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
    x264 ffmpeg \
    && rm -rf /var/lib/apt/lists/*
# log4cplus / OpenSSL / curl come from apt here, matched by
# -DBUILD_DEPENDENCIES=OFF below.
ARG BUILD_JOBS=2
# NOT $(nproc). Docker Desktop reports every host core while granting a
# fraction of host RAM — budget roughly 1.5 GB per parallel compile job.
RUN git clone --depth 1 \
      https://github.com/awslabs/amazon-kinesis-video-streams-producer-sdk-cpp.git \
      /opt/kvs-sdk \
    && mkdir -p /opt/kvs-sdk/build \
    && cd /opt/kvs-sdk/build \
    && cmake .. -DBUILD_GSTREAMER_PLUGIN=ON -DBUILD_DEPENDENCIES=OFF \
    && make -j"${BUILD_JOBS}"
ENV GST_PLUGIN_PATH=/opt/kvs-sdk/build
# kvssink is not part of stock GStreamer -- this is the line that makes
# `gst-inspect-1.0 kvssink` resolve at all inside a container from this image.
WORKDIR /app
ENTRYPOINT ["gst-launch-1.0", "-q"]
