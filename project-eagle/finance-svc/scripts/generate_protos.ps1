# Generate protobuf Go code for finance-svc
# Requires: protoc, protoc-gen-go, protoc-gen-go-grpc

$PROTO_DIR = "proto"
$OUTPUT_DIR = "proto/finance/v1"

# Ensure output directory exists
New-Item -ItemType Directory -Force -Path $OUTPUT_DIR | Out-Null

# Generate Go code from proto files
protoc --proto_path=$PROTO_DIR `
       --go_out=$OUTPUT_DIR `
       --go_opt=paths=source_relative `
       --go-grpc_out=$OUTPUT_DIR `
       --go-grpc_opt=paths=source_relative `
       finance.proto

Write-Host "Protobuf code generation completed."