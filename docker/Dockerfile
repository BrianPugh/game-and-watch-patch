FROM ubuntu:20.04
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update -y && \
    apt-get install --no-install-recommends -yqq \
    make \
    python3 \
    python3-pip \
    wget \
    unzip \
    libftdi1 libftdi1-2 libhidapi-hidraw0 libusb-0.1-4 libusb-1.0-0 \
    git && \
    wget https://nightly.link/kbeckmann/ubuntu-openocd-git-builder/workflows/docker/master/openocd-git.deb.zip && \
    unzip openocd-git.deb.zip && \
    dpkg -i openocd-git_*_amd64.deb && \
    rm openocd-git.deb.zip && \
    rm openocd-git_*_amd64.deb && \
    cd /root && \
    git clone https://github.com/BrianPugh/game-and-watch-patch.git && \
    cd /opt && \
    wget "https://developer.arm.com/-/media/Files/downloads/gnu-rm/10.3-2021.10/gcc-arm-none-eabi-10.3-2021.10-x86_64-linux.tar.bz2" && \
    tar -jxf gcc-arm-none-eabi-10.3-2021.10-x86_64-linux.tar.bz2 && \
    rm gcc-arm-none-eabi-10.3-2021.10-x86_64-linux.tar.bz2
WORKDIR /root/game-and-watch-patch
