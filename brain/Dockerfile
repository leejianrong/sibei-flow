# Multi-stage build for the Rust brain. Build context = ./brain.
FROM rust:1-slim-bookworm AS build
WORKDIR /app
# A C toolchain covers crates with build scripts; rustls keeps us off OpenSSL.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*
COPY . .
RUN cargo build --release --bin brain

FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /app/target/release/brain /usr/local/bin/brain
EXPOSE 8080
CMD ["brain"]
