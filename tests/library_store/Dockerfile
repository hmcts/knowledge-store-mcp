# The image the contract stores are generated and checked in
# (`generate.py --in-container`). Every tool whose behaviour reaches a stored
# byte comes from here, never from the host:
#
# - git, which makes every synthetic commit and whose `%cI` dates the library
#   reads back: built from the release tarball at GIT_VERSION, checked against
#   its published sha256;
# - gzip, which recompresses the graph (`pipeline.py`'s `gzip -9 -n`): the base
#   image's, fixed by the digest below and checked at GZIP_VERSION;
# - the interpreter, and the SQLite and zlib built into it, which write the
#   index and every `.gz` layer the library writes itself: uv's managed CPython
#   at PYTHON_VERSION, the build uv UV_VERSION downloads for it. No other
#   interpreter is installed for uv to find, and none is downloaded later.
#
# The base is pinned by its multi-arch index digest, so arm64 and amd64 hosts
# start from the same Debian release, package for package. A change here changes
# this file's sha256, which every frozen `requirements.txt` records as
# `# dockerfile:`; `generate.py --check` prints both when they differ.
FROM ghcr.io/astral-sh/uv:0.12.17-trixie@sha256:c573e7ef44ae1b3a2faa60ceacc1f22e2bfb5f036373402bfa17b4b6d12db3e8

ARG UV_VERSION=0.12.17
ARG GIT_VERSION=2.55.0
ARG GIT_SHA256=457fdb04dc8728e007d4688695e6912e6f680727920f2a40bf11eacc17505357
ARG GZIP_VERSION=1.13
ARG PYTHON_VERSION=3.14.7

# git's build needs a compiler and curl's headers (git-remote-https: uv fetches
# the library's tags over https). They stay installed: nothing at run time uses
# them, and removing them would take curl's runtime library with them.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl xz-utils make gcc libc6-dev \
      libcurl4-openssl-dev libexpat1-dev libssl-dev zlib1g-dev \
 && rm -rf /var/lib/apt/lists/*

# NO_RUST: the Rust parts (checksums, hashing, loose objects, varints) need a
# Rust toolchain to build and change no output a store records.
RUN curl -fsSLo /tmp/git.tar.xz \
      "https://mirrors.edge.kernel.org/pub/software/scm/git/git-${GIT_VERSION}.tar.xz" \
 && echo "${GIT_SHA256}  /tmp/git.tar.xz" | sha256sum -c - \
 && mkdir /tmp/git \
 && tar -xJf /tmp/git.tar.xz -C /tmp/git --strip-components=1 \
 && make -C /tmp/git -j"$(nproc)" prefix=/usr/local \
      NO_RUST=1 NO_TCLTK=1 NO_GETTEXT=1 NO_PERL=1 NO_PYTHON=1 INSTALL_SYMLINKS=1 \
      all install >/dev/null \
 && rm -rf /tmp/git /tmp/git.tar.xz

# Only uv's managed interpreters, installed here, are ever used.
ENV UV_PYTHON_INSTALL_DIR=/opt/uv/python \
    UV_PYTHON_PREFERENCE=only-managed \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_CONFIG=1 \
    PATH=/usr/local/bin:$PATH
RUN UV_PYTHON_DOWNLOADS=manual uv python install "${PYTHON_VERSION}" \
 && chmod -R a+rX /opt/uv

# Refuse to build an image that is not what this file says.
RUN test "$(uv --version | cut -d' ' -f2)" = "${UV_VERSION}" \
 && test "$(command -v git)" = /usr/local/bin/git \
 && test "$(git --version)" = "git version ${GIT_VERSION}" \
 && test "$(gzip --version | head -n1)" = "gzip ${GZIP_VERSION}" \
 && test "$(uv run --no-project --python "${PYTHON_VERSION}" python -c \
      'import platform; print(platform.python_version())')" = "${PYTHON_VERSION}"

# The file this image was built from, which `generate.py` hashes to say which
# image it is running in, and whose presence says it is in one at all.
COPY Dockerfile /opt/library-store-generator/Dockerfile
