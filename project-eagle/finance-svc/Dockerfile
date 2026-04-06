# Build stage
FROM golang:1.21-alpine AS builder

WORKDIR /app

# Install build dependencies
RUN apk add --no-cache git protobuf protobuf-dev

# Install protoc plugins
RUN go install google.golang.org/protobuf/cmd/protoc-gen-go@v1.31.0 && \
    go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@v1.3.0

# Copy source code
COPY . .

# Generate protobuf code
RUN protoc --proto_path=proto \
           --go_out=proto/finance/v1 \
           --go_opt=paths=source_relative \
           --go-grpc_out=proto/finance/v1 \
           --go-grpc_opt=paths=source_relative \
           finance.proto

# Build the application
RUN CGO_ENABLED=0 GOOS=linux go build -a -installsuffix cgo -o /finance-svc ./cmd/server

# Runtime stage
FROM alpine:latest

RUN apk --no-cache add ca-certificates

WORKDIR /root/

# Copy the binary from builder
COPY --from=builder /finance-svc .

# Expose gRPC port
EXPOSE 50051

# Run the service
CMD ["./finance-svc"]