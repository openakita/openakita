# File Handling Reference

Upload, download, and manage files with the Python SDK.

## Automatic File Upload

Local file paths in input are automatically uploaded:

```python
from inferencesh import inference

client = inference(api_key="inf_...")

# File path is auto-uploaded
result = client.run({
    "app": "image-processor",
    "input": {
        "image": "/path/to/image.png"
    }
})
```

## Manual File Upload

### Basic Upload

```python
file = client.upload_file("/path/to/image.png")
print(file["uri"])  # inf://files/abc123

result = client.run({
    "app": "image-processor",
    "input": {"image": file["uri"]}
})
```

### Upload Options

```python
from inferencesh import UploadFileOptions

file = client.upload_file(
    "/path/to/document.pdf",
    UploadFileOptions(
        filename="custom_name.pdf",      # Custom filename
        content_type="application/pdf",   # MIME type
        path="/documents/reports",        # Storage path
        public=True                       # Publicly accessible
    )
)
```

## Supported Input Types

### File Path

```python
result = client.run({
    "app": "processor",
    "input": {"file": "/path/to/file.png"}
})
```

### Data URI (Base64)

```python
import base64

with open("image.png", "rb") as f:
    b64 = base64.b64encode(f.read()).decode()

result = client.run({
    "app": "processor",
    "input": {"image": f"data:image/png;base64,{b64}"}
})
```

### Bytes

```python
with open("image.png", "rb") as f:
    data = f.read()

file = client.upload_file(data, UploadFileOptions(
    filename="image.png",
    content_type="image/png"
))
```

### File Object

```python
with open("image.png", "rb") as f:
    file = client.upload_file(f, UploadFileOptions(
        filename="image.png",
        content_type="image/png"
    ))
```

## Working with URLs

Use remote URLs directly (no upload needed):

```python
result = client.run({
    "app": "image-processor",
    "input": {
        "image": "https://example.com/image.png"
    }
})
```

## Multiple Files

```python
# Upload multiple files
files = []
for path in ["/path/to/file1.png", "/path/to/file2.png"]:
    file = client.upload_file(path)
    files.append(file["uri"])

result = client.run({
    "app": "multi-file-processor",
    "input": {"images": files}
})
```

## File Info

```python
file = client.upload_file("/path/to/image.png")

print(f"URI: {file['uri']}")
print(f"URL: {file['url']}")  # Direct access URL
print(f"Size: {file['size']}")
print(f"Type: {file['content_type']}")
```

## Downloading Results

```python
import requests

result = client.run({
    "app": "infsh/flux-schnell",
    "input": {"prompt": "A sunset"}
})

# Result contains URL to generated file
image_url = result["output"]["image"]

# Download the file
response = requests.get(image_url)
with open("output.png", "wb") as f:
    f.write(response.content)
```

## Async File Operations

```python
from inferencesh import async_inference
import asyncio
import aiohttp

async def process_files():
    client = async_inference(api_key="inf_...")

    # Upload
    file = await client.upload_file("/path/to/image.png")

    # Process
    result = await client.run({
        "app": "image-processor",
        "input": {"image": file["uri"]}
    })

    # Download result
    async with aiohttp.ClientSession() as session:
        async with session.get(result["output"]["url"]) as resp:
            data = await resp.read()
            with open("output.png", "wb") as f:
                f.write(data)

asyncio.run(process_files())
```

## Agent File Attachments

```python
agent = client.agent("my-org/assistant@latest")

# From bytes
with open("image.png", "rb") as f:
    response = agent.send_message(
        "What's in this image?",
        files=[f.read()]
    )

# From base64
response = agent.send_message(
    "Analyze this document",
    files=["data:application/pdf;base64,JVBERi0xLj..."]
)

# Multiple files
with open("img1.png", "rb") as f1, open("img2.png", "rb") as f2:
    response = agent.send_message(
        "Compare these images",
        files=[f1.read(), f2.read()]
    )
```

## Large File Handling

For large files, use chunked upload:

```python
def upload_large_file(client, filepath, chunk_size=5*1024*1024):
    """Upload large file in chunks (5MB default)."""
    import os

    file_size = os.path.getsize(filepath)
    filename = os.path.basename(filepath)

    with open(filepath, 'rb') as f:
        # Initialize multipart upload
        upload = client.create_multipart_upload(
            filename=filename,
            content_type="application/octet-stream",
            size=file_size
        )

        parts = []
        part_number = 1

        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break

            part = client.upload_part(
                upload_id=upload["id"],
                part_number=part_number,
                data=chunk
            )
            parts.append(part)
            part_number += 1

        # Complete upload
        file = client.complete_multipart_upload(
            upload_id=upload["id"],
            parts=parts
        )

        return file
```

## Content Type Detection

```python
import mimetypes

def upload_with_auto_type(client, filepath):
    content_type, _ = mimetypes.guess_type(filepath)

    return client.upload_file(
        filepath,
        UploadFileOptions(
            content_type=content_type or "application/octet-stream"
        )
    )
```

## Temporary Files

```python
import tempfile
import os

# Create temp file for processing
with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
    tmp.write(image_data)
    tmp_path = tmp.name

try:
    result = client.run({
        "app": "image-processor",
        "input": {"image": tmp_path}
    })
finally:
    os.unlink(tmp_path)  # Clean up
```

## Error Handling

```python
from inferencesh import FileUploadError

try:
    file = client.upload_file("/path/to/large_file.bin")
except FileUploadError as e:
    if "too large" in str(e):
        print("File exceeds size limit")
    elif "unsupported" in str(e):
        print("File type not supported")
    else:
        print(f"Upload failed: {e}")
```
