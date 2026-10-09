# Moov imagecashletter built natively (upstream publishes amd64 only, and its Go runtime
# crashes under emulation on arm64 hosts).

FROM golang:1.26 AS build
RUN CGO_ENABLED=0 GOBIN=/out go install github.com/moov-io/imagecashletter/cmd/server@v0.17.0
FROM gcr.io/distroless/static
COPY --from=build /out/server /server
EXPOSE 8083 9093
ENTRYPOINT ["/server"]
