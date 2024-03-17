FROM ubuntu:22.04
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update -y && apt-get install --no-install-recommends -yqq \
    build-essential \
    make \
    python3 \
    python3-dev \
    python3-pip \
    unzip \
    wget \
    libftdi1 libftdi1-2 libhidapi-hidraw0 libusb-0.1-4 libusb-1.0-0 \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY . /root/game-and-watch-patch

WORKDIR /root/game-and-watch-patch

RUN pip install -r requirements.txt \
    && pip install gnwmanager

RUN gnwmanager install openocd arm-toolchain
