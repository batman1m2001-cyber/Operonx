# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.6.x   | :white_check_mark: |
| < 0.6   | :x:                |

## Reporting a Vulnerability

We take security vulnerabilities seriously. If you discover a security issue, please follow these steps:

### Do NOT

- Open a public GitHub issue for security vulnerabilities
- Disclose the vulnerability publicly before it's fixed

### Do

1. **Email** the maintainers directly (or open a private security advisory on GitHub)
2. **Include** the following information:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Any suggested fixes (optional)

### What to Expect

- **Acknowledgment**: Within 48 hours
- **Initial Assessment**: Within 1 week
- **Resolution Timeline**: Depends on severity
  - Critical: ASAP (target: 1-2 weeks)
  - High: 2-4 weeks
  - Medium/Low: Next release cycle

### After Resolution

- We will notify you when the fix is released
- We will credit you in the release notes (unless you prefer to remain anonymous)

## Security Best Practices for Users

When using Operonx in production:

1. **API Keys**: Never commit API keys to version control
   - Use environment variables or secrets management
   - See `env.example` for the template

2. **Dependencies**: Keep dependencies updated
   - Run `uv sync` regularly
   - Review security advisories

3. **Observability**: Be mindful of what you log
   - Langfuse traces may contain sensitive data
   - Configure trace retention appropriately

## Scope

This security policy applies to:

- The `operonx` Python package on PyPI (and all its extras: `[standard]`, `[anthropic]`, `[onnx]`, etc.)
- The `operonx` and `operonx-macros` Rust crates on crates.io

Third-party provider SDKs (OpenAI, Anthropic, etc.) have their own security policies — vulnerabilities specific to those SDKs should be reported upstream.
