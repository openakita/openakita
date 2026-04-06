# Snapshot and Refs

Compact element references that reduce context usage for AI agents.

**Related**: [commands.md](commands.md) for full function reference, [SKILL.md](../SKILL.md) for quick start.

## Contents

- [How Refs Work](#how-refs-work)
- [Snapshot Output Format](#snapshot-output-format)
- [Using Refs](#using-refs)
- [Ref Lifecycle](#ref-lifecycle)
- [Best Practices](#best-practices)
- [Ref Notation Details](#ref-notation-details)
- [Troubleshooting](#troubleshooting)

## How Refs Work

Traditional approach:
```
Full DOM/HTML -> AI parses -> CSS selector -> Action (~3000-5000 tokens)
```

agent-browser approach:
```
Compact snapshot -> @refs assigned -> Direct interaction (~200-400 tokens)
```

The snapshot extracts interactive elements and assigns short `@e` refs, reducing token usage significantly.

## Snapshot Output Format

```bash
infsh app run agent-browser --function snapshot --session $SESSION --input '{}'
```

**Response `elements_text`:**

```
@e1 [a] "Home" href="/"
@e2 [a] "Products" href="/products"
@e3 [a] "About" href="/about"
@e4 [button] "Sign In"
@e5 [input type="email"] placeholder="Email"
@e6 [input type="password"] placeholder="Password"
@e7 [button type="submit"] "Log In"
@e8 [input type="checkbox"] name="remember"
```

**Response `elements` (structured):**

```json
[
  {
    "ref": "@e1",
    "desc": "@e1 [a] \"Home\" href=\"/\"",
    "tag": "a",
    "text": "Home",
    "role": null,
    "name": null,
    "href": "/",
    "input_type": null
  },
  ...
]
```

## Using Refs

Once you have refs, interact directly:

```bash
# Click the "Sign In" button
'{"action": "click", "ref": "@e4"}'

# Fill email input
'{"action": "fill", "ref": "@e5", "text": "user@example.com"}'

# Fill password
'{"action": "fill", "ref": "@e6", "text": "password123"}'

# Submit the form
'{"action": "click", "ref": "@e7"}'

# Check the "remember me" checkbox
'{"action": "check", "ref": "@e8"}'
```

## Ref Lifecycle

**IMPORTANT**: Refs are invalidated when the page changes!

```bash
# Get initial snapshot
infsh app run agent-browser --function snapshot --session $SESSION --input '{}'
# @e1 [button] "Next"

# Click triggers page change
infsh app run agent-browser --function interact --session $SESSION --input '{
  "action": "click", "ref": "@e1"
}'

# MUST re-snapshot to get new refs!
infsh app run agent-browser --function snapshot --session $SESSION --input '{}'
# @e1 [h1] "Page 2"  <- Different element now!
```

### When to Re-snapshot

Always re-snapshot after:

1. **Navigation** - Clicking links, form submissions, `goto` action
2. **Dynamic content** - AJAX loads, modals opening, tabs switching
3. **Page mutations** - JavaScript modifying the DOM

The `interact` function returns a fresh snapshot in its response, so you can often use that instead of a separate snapshot call.

## Best Practices

### 1. Always Use the Latest Snapshot

```bash
# CORRECT: Use snapshot from previous response
RESULT=$(infsh app run agent-browser --function interact --session $SESSION --input '{
  "action": "click", "ref": "@e1"
}')
# Use elements from $RESULT.snapshot for next action

# WRONG: Using stale refs
# After navigation, @e1 may point to a completely different element
```

### 2. Check Success Before Continuing

```bash
RESULT=$(infsh app run agent-browser --function interact --session $SESSION --input '{
  "action": "click", "ref": "@e5"
}')

SUCCESS=$(echo $RESULT | jq -r '.success')
if [ "$SUCCESS" != "true" ]; then
  echo "Click failed: $(echo $RESULT | jq -r '.message')"
  # Re-snapshot and retry
fi
```

### 3. Use elements_text for Quick Decisions

For AI agents, `elements_text` provides a compact text representation:

```
@e1 [input type="email"] placeholder="Email"
@e2 [input type="password"] placeholder="Password"
@e3 [button] "Submit"
```

This is often enough to decide which element to interact with without parsing the full `elements` array.

## Ref Notation Details

```
@e1 [tag type="value"] "text content" name="attr"
|    |   |             |              |
|    |   |             |              +- Additional attributes
|    |   |             +- Visible text
|    |   +- Key attributes shown
|    +- HTML tag name
+- Unique ref ID
```

### Common Patterns

```
@e1 [button] "Submit"                    # Button with text
@e2 [input type="email"]                 # Email input
@e3 [input type="password"]              # Password input
@e4 [a] "Link Text" href="/page"         # Anchor link
@e5 [select]                             # Dropdown
@e6 [textarea] placeholder="Message"     # Text area
@e7 [input type="file"]                  # File upload
@e8 [input type="checkbox"] checked      # Checked checkbox
@e9 [input type="radio"] selected        # Selected radio
@e10 [button type="submit"] "Send"       # Submit button
```

### Elements Captured

The snapshot captures these interactive elements:

- Links (`<a href>`)
- Buttons (`<button>`, `[role="button"]`)
- Inputs (`<input>`, `<textarea>`, `<select>`)
- Clickable elements (`[onclick]`, `[tabindex]`)
- ARIA roles (`[role="link"]`, `[role="checkbox"]`, etc.)

Non-interactive or hidden elements are filtered out.

## Troubleshooting

### "Unknown ref" Error

```json
{
  "success": false,
  "message": "Unknown ref: @e15. Run 'snapshot' to get current elements."
}
```

**Solution**: Re-snapshot. The page changed and refs are stale.

```bash
infsh app run agent-browser --function snapshot --session $SESSION --input '{}'
# Now use the new refs
```

### Element Not in Snapshot

The element you need might not appear because:

1. **Not visible** - Scroll to reveal it
   ```bash
   '{"action": "scroll", "direction": "down", "scroll_amount": 500}'
   ```

2. **Not interactive** - Use JavaScript to interact
   ```bash
   '{"code": "document.querySelector(\".hidden-btn\").click()"}'
   ```

3. **In iframe** - Currently not supported (use `execute` with JS)

4. **Dynamic** - Wait for it to load
   ```bash
   '{"action": "wait", "wait_ms": 2000}'
   ```

### Too Many Elements

Snapshots are limited to 50 elements. If the page has more:

1. **Scroll** to bring relevant elements into view
2. **Use JavaScript** to target specific elements
3. **Navigate** to a more specific page

### Ref Points to Wrong Element

If a ref seems to interact with the wrong element:

1. Re-snapshot to get fresh refs
2. Check if the page structure changed
3. Verify with screenshot that the right element is targeted
