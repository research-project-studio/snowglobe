# v0.3.1 Implementation: Quick Fixes

## For Claude Code

Two fixes for this release:
1. Fix double save dialog
2. Add page reload on capture start

---

## Fix 1: Double Save Dialog

### Task
Find and fix the duplicate download trigger that causes two save dialogs to appear.

### Debugging Steps

First, add logging to identify the source:

**File:** `extension/src/devtools/panel.ts`

Find all places where download/save is triggered. Add logging:

```typescript
// Wherever download is triggered, add:
console.log('[Download] Triggered from:', new Error().stack);
```

### Common Causes & Fixes

**Cause A: Duplicate event listener registration**

```typescript
// PROBLEM - handler added multiple times
function setupUI() {
    saveButton.addEventListener('click', handleSave);
}
// If setupUI() is called multiple times, handlers stack up

// FIX - Remove before adding, or check if already added
let saveHandlerAttached = false;

function setupUI() {
    if (!saveHandlerAttached) {
        saveButton.addEventListener('click', handleSave);
        saveHandlerAttached = true;
    }
}

// OR use named function and removeEventListener
function setupUI() {
    saveButton.removeEventListener('click', handleSave);
    saveButton.addEventListener('click', handleSave);
}
```

**Cause B: Multiple code paths triggering download**

```typescript
// PROBLEM - download triggered in callback AND then block
async function processAndDownload() {
    const result = await processCapture();
    downloadArchive(result.url);  // First trigger
    
    if (result.success) {
        downloadArchive(result.url);  // Second trigger!
    }
}

// FIX - single download point
async function processAndDownload() {
    const result = await processCapture();
    if (result.success) {
        downloadArchive(result.url);  // Only here
    }
}
```

**Cause C: Both message handler AND direct call**

```typescript
// PROBLEM
chrome.runtime.onMessage.addListener((msg) => {
    if (msg.type === 'download') {
        triggerDownload(msg.url);
    }
});

// And somewhere else:
async function handleProcessComplete(url) {
    chrome.runtime.sendMessage({ type: 'download', url });  // Triggers listener
    triggerDownload(url);  // Also triggers directly!
}

// FIX - use one or the other, not both
async function handleProcessComplete(url) {
    triggerDownload(url);  // Direct only
}
```

### Search Patterns

Search the codebase for these patterns to find download triggers:

```
chrome.downloads.download
window.open.*blob
URL.createObjectURL
saveAs
download(
```

### Implementation

1. Add logging to identify duplicate
2. Run capture and check console for two "[Download]" logs
3. Fix based on the stack traces showing where each originates
4. Remove logging after fix confirmed

---

## Fix 2: Page Reload on Capture Start

### Task
Automatically reload the page when capture starts to ensure sprites and glyphs are captured.

### Implementation

**File:** `extension/src/devtools/panel.ts`

Find the `startCapture()` function (or equivalent) and modify:

```typescript
// Add at the top of the file or in appropriate scope
let reloadOnCaptureStart = true;  // Default ON

async function startCapture() {
    // Clear any previous capture data
    capturedTiles = [];
    capturedSprites = [];
    capturedGlyphs = [];
    // ... any other state reset
    
    // Update UI
    updateStatus('Starting capture...');
    
    // Reload page to capture initial resources (sprites, glyphs, style)
    if (reloadOnCaptureStart) {
        updateStatus('Reloading page to capture all resources...');
        
        try {
            // Reload the inspected window, bypassing cache
            await chrome.devtools.inspectedWindow.reload({
                ignoreCache: true
            });
            
            // Brief delay to let reload initiate before we start listening
            await new Promise(resolve => setTimeout(resolve, 300));
        } catch (error) {
            console.error('[WebMap Archiver] Failed to reload page:', error);
            // Continue anyway - capture what we can
        }
    }
    
    // Now start capturing
    isCapturing = true;
    updateStatus('Capturing... Pan and zoom the map to capture tiles.');
    
    // Update button states
    updateButtonStates();
}
```

### API Reference

`chrome.devtools.inspectedWindow.reload(options)`:
- `ignoreCache: true` - Bypass browser cache to get fresh resources
- `userAgent: string` - Optional custom user agent
- `injectedScript: string` - Optional script to inject on reload

### UI Addition (Optional for v0.3.1, can defer to v0.3.2)

Add checkbox to panel.html:

```html
<div class="option">
    <label>
        <input type="checkbox" id="reload-on-capture" checked>
        Reload page on capture start
    </label>
</div>
```

And in panel.ts:

```typescript
function getReloadSetting(): boolean {
    const checkbox = document.getElementById('reload-on-capture') as HTMLInputElement;
    return checkbox ? checkbox.checked : true;  // Default true if element missing
}

async function startCapture() {
    reloadOnCaptureStart = getReloadSetting();
    // ... rest of function
}
```

### Testing

1. Navigate to parkingregulations.nyc
2. Open DevTools → WebMap Archiver panel
3. Click "Start Capture"
4. **Verify**: Page reloads automatically
5. **Verify**: Console shows "Reloading page to capture all resources..."
6. **Verify**: After reload, status shows "Capturing..."
7. Pan/zoom map
8. Stop capture and process
9. **Verify**: Sprites and glyphs are in the archive

---

## Testing Checklist

### Double Save Fix
- [ ] Click save/download
- [ ] Only ONE save dialog appears
- [ ] File saves correctly
- [ ] No console errors

### Page Reload
- [ ] Start capture triggers page reload
- [ ] Reload bypasses cache (check Network tab)
- [ ] Capture continues after reload
- [ ] Sprites captured (check bundle)
- [ ] Glyphs captured (check bundle)
- [ ] If checkbox added: unchecking prevents reload

---

## Files to Modify

| File | Changes |
|------|---------|
| `extension/src/devtools/panel.ts` | Fix double download, add reload logic |
| `extension/src/devtools/panel.html` | (Optional) Add reload checkbox |

---

## Commit Message

```
fix(extension): fix double save dialog and add page reload on capture

- Fix duplicate download trigger causing two save dialogs
- Add automatic page reload when capture starts to ensure sprites/glyphs captured
- Reload bypasses cache to get fresh resources

Closes #X (if applicable)
```