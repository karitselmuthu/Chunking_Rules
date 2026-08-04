# Configuration Guide

## Core LLM Configuration

The agentic chunking strategy now uses Gemini directly over the Google
Generative Language API. Configure the provider and model in `.env`:

```env
CHUNKING_LLM_MODEL=gemini-2.5-flash
GEMINI_API_KEY=your-gemini-api-key
```

- `GEMINI_API_KEY` enables real agentic chunking.
- When `GEMINI_API_KEY` is not set, the app falls back to rule-based chunking.
- `CHUNKING_LLM_MODEL` selects the Gemini model used by `agentic_chunk()`.

## LLM Sampling Parameters

The application now supports configurable sampling parameters for the LLM-based agentic chunking strategy. These parameters control how the Gemini language model generates chunks.

### Environment Variables

Add these to your `.env` file to customize LLM sampling behavior:

```env
# Top-P (Nucleus Sampling)
# Controls diversity of output via nucleus sampling
# Valid range: 0.0 to 1.0
# Default: 1.0 (disabled)
CHUNKING_LLM_TOP_P=1.0

# Top-K
# Limits sampling to only top-k most likely tokens
# Valid range: 0 to any positive integer (0 = disabled)
# Default: 0 (disabled)
CHUNKING_LLM_TOP_K=0
```

### Parameter Explanations

#### `CHUNKING_LLM_TOP_P` (Top-P / Nucleus Sampling)
- **What it does**: Includes tokens whose cumulative probability reaches the threshold
- **Range**: 0.0 - 1.0
- **Default**: 1.0 (all tokens considered)
- **Use cases**:
  - `0.9` - Balances variety and coherence (recommended for creative outputs)
  - `0.7` - More focused, less random
  - `1.0` - Full model distribution (standard behavior)

#### `CHUNKING_LLM_TOP_K` (Top-K Sampling)
- **What it does**: Only considers the k most likely next tokens
- **Range**: 0 (disabled) or positive integer
- **Default**: 0 (disabled)
- **Use cases**:
  - `40` - Limits to top 40 tokens (good for structured tasks)
  - `0` - No limit (standard behavior)

### Recommended Configurations

**For Deterministic/Structured Chunks:**
```env
CHUNKING_LLM_TOP_P=0.7
CHUNKING_LLM_TOP_K=0
```

**For More Variety/Creative Interpretation:**
```env
CHUNKING_LLM_TOP_P=0.95
CHUNKING_LLM_TOP_K=40
```

**For Maximum Consistency (Default):**
```env
CHUNKING_LLM_TOP_P=1.0
CHUNKING_LLM_TOP_K=0
```

### Implementation Details

- These parameters are only used by the `agentic_chunk()` strategy
- They are read from environment variables at runtime
- If not specified, sensible defaults are applied
- The implementation gracefully handles invalid values by using defaults
- Parameters are conditionally included in the API call to avoid conflicts

### Example Usage

1. **Copy the example environment file:**
   ```bash
   cp .env.example .env
   ```

2. **Edit .env with your desired sampling parameters:**
   ```env
   GEMINI_API_KEY=your-gemini-api-key
   CHUNKING_LLM_MODEL=gemini-2.5-flash
   CHUNKING_LLM_TOP_P=0.9
   CHUNKING_LLM_TOP_K=40
   ```

3. **Use the app normally** - the parameters will be automatically loaded and applied to LLM calls

### Technical Notes

- Parameters are loaded as floats/ints with appropriate type conversion
- Invalid values will fall back to defaults without crashing
- The Gemini API validates sampling parameters at request time
- Top-p and top-k can be used together or independently

## S3 Input Configuration

The app can also load input directly from Amazon S3 by sending an object URI
like `s3://bucket/path/file.pdf` or a prefix ending in `/`.

```env
AWS_REGION=us-east-1
AWS_PROFILE=default
```

- `AWS_REGION` sets the default region for S3 requests when the UI/API request
  does not provide one explicitly.
- `AWS_PROFILE` selects a named local AWS profile for `boto3`.
- Standard AWS credential resolution still works (`aws configure`, environment
  variables, IAM role, or container/task credentials).
