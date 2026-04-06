# File Handling Reference

Upload, download, and manage files with the JavaScript SDK.

## Automatic File Upload

Local file paths in input are automatically uploaded (Node.js):

```typescript
import { inference } from '@inferencesh/sdk';

const client = inference({ apiKey: 'inf_...' });

// File path is auto-uploaded
const result = await client.run({
  app: 'image-processor',
  input: {
    image: '/path/to/image.png'
  }
});
```

## Manual File Upload

### Node.js

```typescript
// Basic upload
const file = await client.uploadFile('/path/to/image.png');
console.log(file.uri); // inf://files/abc123

const result = await client.run({
  app: 'image-processor',
  input: { image: file.uri }
});
```

### Upload Options

```typescript
const file = await client.uploadFile('/path/to/document.pdf', {
  filename: 'custom_name.pdf',      // Custom filename
  contentType: 'application/pdf',   // MIME type
  path: '/documents/reports',        // Storage path
  public: true                       // Publicly accessible
});
```

## Browser File Upload

### From File Input

```typescript
const input = document.querySelector<HTMLInputElement>('input[type="file"]');

input.addEventListener('change', async (e) => {
  const file = input.files?.[0];
  if (!file) return;

  const uploaded = await client.uploadFile(file);
  console.log('Uploaded:', uploaded.uri);
});
```

### With React

```typescript
import { useState } from 'react';
import { inference } from '@inferencesh/sdk';

function FileUploader() {
  const [uploading, setUploading] = useState(false);
  const client = inference({ proxyUrl: '/api/inference/proxy' });

  async function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;

    setUploading(true);
    try {
      const uploaded = await client.uploadFile(file);
      console.log('Uploaded:', uploaded.uri);
    } finally {
      setUploading(false);
    }
  }

  return (
    <div>
      <input type="file" onChange={handleFile} disabled={uploading} />
      {uploading && <span>Uploading...</span>}
    </div>
  );
}
```

### Drag and Drop

```typescript
function DropZone() {
  const client = inference({ proxyUrl: '/api/inference/proxy' });

  async function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    const files = Array.from(e.dataTransfer.files);

    for (const file of files) {
      const uploaded = await client.uploadFile(file);
      console.log(`Uploaded ${file.name}:`, uploaded.uri);
    }
  }

  return (
    <div
      onDrop={handleDrop}
      onDragOver={(e) => e.preventDefault()}
      style={{ border: '2px dashed #ccc', padding: 20 }}
    >
      Drop files here
    </div>
  );
}
```

## Supported Input Types

### File Path (Node.js)

```typescript
const result = await client.run({
  app: 'processor',
  input: { file: '/path/to/file.png' }
});
```

### Data URI (Base64)

```typescript
import { readFileSync } from 'fs';

const buffer = readFileSync('image.png');
const b64 = buffer.toString('base64');

const result = await client.run({
  app: 'processor',
  input: { image: `data:image/png;base64,${b64}` }
});
```

### Buffer (Node.js)

```typescript
import { readFileSync } from 'fs';

const buffer = readFileSync('image.png');

const file = await client.uploadFile(buffer, {
  filename: 'image.png',
  contentType: 'image/png'
});
```

### Blob (Browser)

```typescript
// From canvas
const canvas = document.querySelector('canvas');
canvas.toBlob(async (blob) => {
  if (!blob) return;

  const file = await client.uploadFile(blob, {
    filename: 'canvas.png',
    contentType: 'image/png'
  });
  console.log('Uploaded:', file.uri);
});
```

### File Object (Browser)

```typescript
const fileInput = document.querySelector<HTMLInputElement>('input[type="file"]');
const file = fileInput.files?.[0];

if (file) {
  const uploaded = await client.uploadFile(file);
}
```

## Working with URLs

Use remote URLs directly (no upload needed):

```typescript
const result = await client.run({
  app: 'image-processor',
  input: {
    image: 'https://example.com/image.png'
  }
});
```

## Multiple Files

```typescript
// Upload multiple files
const files = await Promise.all([
  client.uploadFile('/path/to/file1.png'),
  client.uploadFile('/path/to/file2.png')
]);

const result = await client.run({
  app: 'multi-file-processor',
  input: { images: files.map(f => f.uri) }
});
```

## File Info

```typescript
const file = await client.uploadFile('/path/to/image.png');

console.log({
  uri: file.uri,        // inf://files/abc123
  url: file.url,        // Direct access URL
  size: file.size,      // File size in bytes
  contentType: file.contentType
});
```

## Downloading Results

### Node.js

```typescript
import { writeFileSync } from 'fs';

const result = await client.run({
  app: 'infsh/flux-schnell',
  input: { prompt: 'A sunset' }
});

// Result contains URL to generated file
const imageUrl = result.output.image;

// Download the file
const response = await fetch(imageUrl);
const buffer = Buffer.from(await response.arrayBuffer());
writeFileSync('output.png', buffer);
```

### Browser

```typescript
const result = await client.run({
  app: 'infsh/flux-schnell',
  input: { prompt: 'A sunset' }
});

// Trigger download
const link = document.createElement('a');
link.href = result.output.image;
link.download = 'generated.png';
link.click();
```

## Agent File Attachments

### Node.js

```typescript
import { readFileSync } from 'fs';

const agent = client.agent('my-org/assistant@latest');

// From buffer
const response = await agent.sendMessage('What\'s in this image?', {
  files: [readFileSync('image.png')]
});
```

### Browser

```typescript
const agent = client.agent('my-org/assistant@latest');

// From file input
const input = document.querySelector<HTMLInputElement>('input[type="file"]');
const file = input.files?.[0];

if (file) {
  const response = await agent.sendMessage('Describe this image', {
    files: [file]
  });
}
```

### From Base64

```typescript
const response = await agent.sendMessage('Analyze this document', {
  files: ['data:application/pdf;base64,JVBERi0xLj...']
});
```

### Multiple Files

```typescript
const response = await agent.sendMessage('Compare these images', {
  files: [
    await fetch('image1.png').then(r => r.blob()),
    await fetch('image2.png').then(r => r.blob())
  ]
});
```

## Content Type Detection

```typescript
function getContentType(filename: string): string {
  const ext = filename.split('.').pop()?.toLowerCase();
  const types: Record<string, string> = {
    png: 'image/png',
    jpg: 'image/jpeg',
    jpeg: 'image/jpeg',
    gif: 'image/gif',
    webp: 'image/webp',
    pdf: 'application/pdf',
    mp4: 'video/mp4',
    mp3: 'audio/mpeg',
    wav: 'audio/wav'
  };
  return types[ext || ''] || 'application/octet-stream';
}

async function uploadWithAutoType(path: string) {
  const filename = path.split('/').pop() || 'file';
  return client.uploadFile(path, {
    contentType: getContentType(filename)
  });
}
```

## React Hook for File Upload

```typescript
import { useState, useCallback } from 'react';
import { inference } from '@inferencesh/sdk';

interface UploadState {
  uploading: boolean;
  progress: number;
  error: string | null;
  file: any | null;
}

function useFileUpload() {
  const [state, setState] = useState<UploadState>({
    uploading: false,
    progress: 0,
    error: null,
    file: null
  });

  const client = inference({ proxyUrl: '/api/inference/proxy' });

  const upload = useCallback(async (file: File) => {
    setState({ uploading: true, progress: 0, error: null, file: null });

    try {
      const uploaded = await client.uploadFile(file, {
        onProgress: (progress) => {
          setState(prev => ({ ...prev, progress }));
        }
      });

      setState({ uploading: false, progress: 100, error: null, file: uploaded });
      return uploaded;
    } catch (e: any) {
      setState(prev => ({ ...prev, uploading: false, error: e.message }));
      throw e;
    }
  }, []);

  return { ...state, upload };
}
```

## Error Handling

```typescript
import { FileUploadError } from '@inferencesh/sdk';

try {
  const file = await client.uploadFile(largeFile);
} catch (e) {
  if (e instanceof FileUploadError) {
    if (e.message.includes('too large')) {
      console.log('File exceeds size limit');
    } else if (e.message.includes('unsupported')) {
      console.log('File type not supported');
    } else {
      console.log('Upload failed:', e.message);
    }
  }
}
```

## Stream Upload (Large Files)

```typescript
import { createReadStream } from 'fs';
import { stat } from 'fs/promises';

async function uploadLargeFile(filepath: string) {
  const stats = await stat(filepath);
  const stream = createReadStream(filepath);

  const file = await client.uploadFile(stream, {
    filename: filepath.split('/').pop(),
    contentType: 'application/octet-stream',
    size: stats.size
  });

  return file;
}
```
