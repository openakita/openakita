# Security Policy

## Supported Versions

We release patches for security issues in the following versions:

| Version | Supported          |
| ------- | ------------------ |
| 0.5.x   | :white_check_mark: |
| < 0.5   | :x:                |

## Reporting a Vulnerability

We take security seriously. If you discover a security vulnerability within OpenAkita, please follow these steps:

### 1. Do NOT Create a Public Issue

Please do not report security vulnerabilities through public GitHub issues.

### 2. Email Us Directly

Send an email to **zacon365@gmail.com** with the following information:

- Type of issue (e.g., buffer overflow, API key exposure, command injection, etc.)
- Full paths of source file(s) related to the issue
- The location of the affected source code (tag/branch/commit or direct URL)
- Any special configuration required to reproduce the issue
- Step-by-step instructions to reproduce the issue
- Proof-of-concept or exploit code (if possible)
- Impact of the issue, including how an attacker might exploit it

### 3. Response Timeline

- **Initial Response**: Within 48 hours
- **Status Update**: Within 7 days
- **Resolution**: Depends on complexity, but we aim for 30 days

### 4. Disclosure Policy

- We will confirm receipt of your report within 48 hours
- We will provide an estimated timeline for a fix
- We will notify you when the vulnerability is fixed
- We request that you do not disclose the vulnerability publicly until we have released a fix

## Security Best Practices for Users

### API Key Security

```bash
# NEVER commit your .env file
echo ".env" >> .gitignore

# Use environment variables in production
export ANTHROPIC_API_KEY=your-key

# Rotate keys if you suspect exposure
```

### File System Access

OpenAkita has file system access. To limit exposure:

```bash
# Run in a dedicated directory
mkdir ~/openakita-workspace
cd ~/openakita-workspace

# Consider using Docker for isolation
docker run --rm -it openakita
```

### Network Security

- Use HTTPS endpoints for API communication
- Consider using a firewall to limit outbound connections
- Monitor network traffic for unusual patterns

### Shell Command Execution

OpenAkita can execute shell commands. Recommendations:

- Review commands before execution (disable AUTO_CONFIRM)
- Run with minimal required permissions
- Use a sandboxed environment for untrusted tasks

## Security Features

### Built-in Protections

- **Command Confirmation**: Dangerous commands require user confirmation
- **Path Restrictions**: Sensitive system paths are protected
- **Input Validation**: User inputs are sanitized before processing
- **Rate Limiting**: API calls are rate-limited to prevent abuse

### Configuration Options

```bash
# Require confirmation for potentially dangerous operations
AUTO_CONFIRM=false

# Limit maximum iterations to prevent runaway loops
MAX_ITERATIONS=100

# Enable verbose logging for auditing
LOG_LEVEL=DEBUG
```

## Known Security Considerations

### LLM Prompt Injection

As with any LLM-based system, OpenAkita may be susceptible to prompt injection. Mitigations:

- Validate and sanitize user inputs
- Use the agent in controlled environments
- Monitor outputs for unexpected behavior

### Third-Party Dependencies

- Dependencies are pinned to specific versions
- Regular security audits of dependencies
- Automated vulnerability scanning in CI/CD

## Security Updates

Security updates will be released as:

- Patch releases (e.g., 0.5.1 -> 0.5.2) for minor fixes
- Security advisories on GitHub for critical issues
- Email notifications to registered users (if applicable)

## Contact

For security concerns, contact: **zacon365@gmail.com**

For general questions, use [GitHub Discussions](https://github.com/openakita/openakita/discussions).

---

Thank you for helping keep OpenAkita and its users safe!
