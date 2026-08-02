# Text Style Expansion for codn (Fonts + Position + Effects)

## TL;DR

> **Quick Summary**: Expand the Instagram quote-overlay styling system so the LLM has real typography control: a curated font library with per-font descriptions it can read, precise position fine-tuning, and richer text effects — backed by pytest coverage.
>
> **Deliverables**:
> - `fonts.json` — font manifest with visual descriptions, use-cases, avoid-cases per font
> - `font_registry.py` — single source of truth: loads manifest, resolves fonts, builds LLM prompt text, validates choices
> - New overlay contract v2 fields: `font`, `font_weight`, `x_offset`, `y_offset`, `letter_spacing`, `text_effect` (adds `glow`), `glow_color`, `text_gradient_from/to`
> - ~16 new OFL fonts downloaded to `~/.local/share/fonts`
> - Expanded `text_overlay.py` renderer: bold, precise position, glow, gradient, letter-spacing
> - pytest infra + unit tests for registry, llama_manager parsing, and renderer
>
> **Estimated Effort**: Medium
> **Parallel Execution**: YES - 4 waves
> **Critical Path**: Font download → fonts.json → font_registry → llama_manager prompt + text_overlay renderer → integration QA → F1-F4

---

## Context

### Original Request
User wants the text styling system exposed more richly. The LLM currently picks from 5 coarse `font_style` categories but CANNOT see what fonts look like — it needs text descriptions. Position (bottom/top/center/middle-left/middle-right) is too vague — model needs finer control. Text effects (none/shadow/outline) need more options. Alignment, background overlay, and font size stay unchanged.

### Interview Summary
**Key Discussions**:
- **Position**: Both named buckets + fine-tune → keep named positions, add `x_offset`/`y_offset` (signed percentage of image dims, clamped to margins)
- **Text effects**: Add glow (with `glow_color`), bold weight (`font_weight`), letter-spacing (`letter_spacing` px), gradient fill (`text_gradient_from/to`); keep none/shadow/outline
- **Fonts**: Download new OFL fonts (websearch-informed shortlist); write per-font descriptions because the LLM is blind to appearance
- **Descriptions storage**: `fonts.json` manifest loaded into the LLM system prompt at call time
- **Tests**: YES — add pytest (installed globally, not yet in requirements.txt)

**Research Findings**:
- Websearch (2025-2026 quote-post consensus): Cormorant Garamond, Libre Baskerville, Lora, Crimson Text, EB Garamond, Spectral, DM Serif Display (serif); Inter, Montserrat, Poppins, Raleway, Work Sans, DM Sans (sans); Great Vibes, Allura, Parisienne, Sacramento (script); Bebas Neue, Anton, Abril Fatface (display); JetBrains Mono, IBM Plex Mono, Space Mono (mono). All SIL OFL.
- Existing user fonts: Caveat, DancingScript, Oswald, Pacifico, PlayfairDisplay (variable)
- System fonts: Noto (184 files), OpenSans, RedHat, Liberation, STIX available as fallbacks
- Current contract: `_OVERLAY_SYSTEM` prompt (llama_manager.py:195-214), whitelist `_normalise_overlay` (llama_manager.py:239-267), renderer `add_text_overlay` (text_overlay.py:88-198)
- Pipeline: main.py:213 `generate_text_overlay()` → main.py:226 `add_text_overlay()`
- No test infra; pytest 9.0.3 + pytest-asyncio installed globally

### Metis Review
> Manual substitute (agent unavailable due to billing). Gap analysis performed by Prometheus.
- **Identified Gaps** (addressed):
  - Font descriptions must be condensed (1-2 sentences each) to keep the LLM prompt under the 4096-token context → `font_registry.build_prompt_text()` returns concise per-category summaries
  - Backward compatibility: old overlay dicts without v2 fields must render unchanged → all new fields use `.get()` defaults
  - Off-screen safety: offsets clamped to margins in renderer
  - Font download failure resilience: registry falls back to existing fonts, renderer degrades gracefully

---

## Work Objectives

### Core Objective
Give the LLM true typographic control over quote overlays: a curated, described font library, precise position tuning, and richer text effects — with unit tests protecting the new logic.

### Concrete Deliverables
- `fonts.json` (font manifest with descriptions)
- `font_registry.py` (loader/resolver/prompt-builder/validator)
- `~/.local/share/fonts/` populated with ~16 new OFL fonts
- `llama_manager.py` overlay contract v2 (prompt + whitelist)
- `text_overlay.py` renderer v2 (bold, positions+offsets, glow, gradient, letter-spacing)
- `tests/` directory with pytest unit tests
- `requirements.txt` updated with `pytest`

### Definition of Done
- [ ] `pytest tests/` → all tests pass
- [ ] Sample images rendered exercising EVERY new option (bold, each position+offset, glow, gradient, letter-spacing) — evidence in `.omo/evidence/`
- [ ] LLM prompt contains condensed font descriptions when `generate_text_overlay` builds the request
- [ ] Old-style dicts render without error (backward compat)
- [ ] `main.py` unchanged except nothing (API contract untouched)

### Must Have
- Font descriptions visible to the LLM (via injected prompt text)
- Finer position control via `x_offset`/`y_offset`
- New text effects: glow, bold, letter-spacing, gradient
- pytest unit tests for font_registry, llama_manager parsing/normalisation, text_overlay rendering
- Backward compatibility with existing style dicts

### Must NOT Have (Guardrails)
- NO changes to alignment/background-overlay/font-size behavior
- NO changes to `main.py` API contract or the `/generate` response shape
- NO ComfyUI, Instagram uploader, or database changes
- NO commercial-license fonts — only SIL OFL / Apache-2.0
- NO font description bloat in the LLM prompt (keep < 4096 tokens total)
- NO removing existing font styles or effect names (additive only)
- NO hand-picked "magic" position values — offsets must be model-controlled and clamped

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** - ALL verification is agent-executed. No exceptions.

### Test Decision
- **Infrastructure exists**: NO
- **Automated tests**: YES (tests-after; pytest 9.0.3 already installed)
- **Framework**: pytest
- **Setup**: add `pytest>=8.0` to requirements.txt, create `tests/` with `conftest.py`

### QA Policy
Every task MUST include agent-executed QA scenarios. Evidence saved to `.omo/evidence/task-{N}-{scenario-slug}.{ext}`.
- **Backend/Module**: Bash (python3) — import modules, call functions, render test images, assert output
- **API**: Bash (curl) — hit `/generate` and `/health` where relevant
- **Filesystem**: Bash (ls, fc-scan, python PIL open) — verify font files exist and load

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately - foundation, MAX PARALLEL):
├── Task 1: Download ~16 OFL fonts into ~/.local/share/fonts [quick]
├── Task 2: Create fonts.json manifest (descriptions + file mapping) [writing]
├── Task 3: Create font_registry.py (loader/resolver/prompt-builder/validator) [deep]
└── Task 4: pytest infrastructure (requirements.txt, tests/, conftest.py) [quick]

Wave 2 (After Wave 1 - core modules, PARALLEL):
├── Task 5: llama_manager.py overlay contract v2 (prompt + whitelist) [deep]
└── Task 6: text_overlay.py renderer v2 (bold, positions+offsets, effects) [deep]

Wave 3 (After Wave 2 - integration + tests):
├── Task 7: Unit tests: font_registry + llama_manager normalisation [quick]
├── Task 8: Unit tests: text_overlay renderer (position math, effects) [deep]
└── Task 9: Integration QA — render sample images for every new option [unspecified-high]

Wave FINAL (After ALL tasks — 4 parallel reviews, then user okay):
├── Task F1: Plan compliance audit (oracle)
├── Task F2: Code quality review (unspecified-high)
├── Task F3: Real manual QA (unspecified-high)
└── Task F4: Scope fidelity check (deep)
-> Present results -> Get explicit user okay

Critical Path: Task 1 → Task 2/3 → Task 5/6 → Task 9 → F1-F4 → user okay
Parallel Speedup: ~60% faster than sequential
Max Concurrent: 4 (Waves 1 & 2)
```

### Dependency Matrix
- **1** (fonts download): none — blocks 2 (manifest confirms files), 3 (registry fallbacks), 6 (renderer fonts)
- **2** (fonts.json): 1 — blocks 3 (schema), 5 (prompt text)
- **3** (font_registry.py): 1, 2 — blocks 5 (prompt builder), 6 (resolver), 7 (tests)
- **4** (pytest infra): none — blocks 7, 8 (test execution)
- **5** (llama_manager v2): 2, 3 — blocks 7 (normalisation tests), 9 (integration)
- **6** (text_overlay v2): 1, 3 — blocks 8 (renderer tests), 9 (integration)
- **7** (tests registry+llama): 3, 4, 5 — blocks 9
- **8** (tests renderer): 3, 4, 6 — blocks 9
- **9** (integration QA): 5, 6, 7, 8 — blocks F1-F4

### Agent Dispatch Summary
- **Wave 1**: 4 tasks — T1 → `quick`, T2 → `writing`, T3 → `deep`, T4 → `quick`
- **Wave 2**: 2 tasks — T5 → `deep`, T6 → `deep`
- **Wave 3**: 3 tasks — T7 → `quick`, T8 → `deep`, T9 → `unspecified-high`
- **FINAL**: 4 tasks — F1 → `oracle`, F2 → `unspecified-high`, F3 → `unspecified-high`, F4 → `deep`

---

## TODOs

- [ ] 1. Download ~16 OFL font files into `~/.local/share/fonts`

  **What to do**:
  - Download the shortlisted SIL OFL fonts from Google Fonts GitHub raw URLs (`https://github.com/google/fonts/raw/main/ofl/{family}/...`) into `~/.local/share/fonts/`
  - Font list (all OFL): Cormorant Garamond, Libre Baskerville, Lora, Crimson Text, EB Garamond, Spectral, DM Serif Display (serif); Inter, Montserrat, Poppins, Raleway, Work Sans (sans); Great Vibes, Allura, Parisienne, Sacramento (script); Bebas Neue, Anton, Abril Fatface (display); JetBrains Mono, IBM Plex Mono, Space Mono (mono)
  - Prefer static `.ttf` files over variable fonts where possible (Pillow compatibility); for families where only variable fonts exist (e.g. Inter, Raleway), download the variable `.ttf` and note it
  - Run `fc-cache -f` after download
  - Verify each file loads with Pillow: `python3 -c "from PIL import ImageFont; ImageFont.truetype('path', 40)"`

  **Must NOT do**:
  - Do NOT download any font that is not SIL OFL / Apache-2.0 licensed
  - Do NOT overwrite existing user fonts (Caveat, DancingScript, Oswald, Pacifico, PlayfairDisplay)
  - Do NOT add fonts to system dirs (`/usr/share/fonts`) — user dir only

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Mechanical download + verification task, well-specified URLs
  - **Skills**: []
  - **Skills Evaluated but Omitted**:
    - `git-master`: no git operations needed

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2, 3, 4)
  - **Blocks**: 2 (manifest file paths), 3 (registry fallback), 6 (renderer fonts)
  - **Blocked By**: None (can start immediately)

  **References**:
  - `https://github.com/google/fonts` — Google Fonts repo; raw URLs at `ofl/{family}/{FontName}.ttf`
  - `~/.local/share/fonts/` — target dir (contains existing Caveat-Variable.ttf, PlayfairDisplay-Variable.ttf etc.)
  - `text_overlay.py:12-37` (`_SYSTEM_FONTS`) — existing font path conventions to mirror
  - Google Fonts specimen pages (e.g. `https://fonts.google.com/specimen/Cormorant+Garamond`) to confirm exact family filenames

  **Acceptance Criteria**:
  - [ ] `ls ~/.local/share/fonts/` shows all ~16 new families (>= 20 new files)
  - [ ] Every downloaded file passes `ImageFont.truetype(path, 40)` without error
  - [ ] `fc-cache -f` ran without error

  **QA Scenarios**:
  ```
  Scenario: All fonts download and load
    Tool: Bash (python3 + ls)
    Preconditions: Network access; ~/.local/share/fonts writable
    Steps:
      1. Run `ls ~/.local/share/fonts/` and count new files
      2. Run `python3 -c "from PIL import ImageFont; import glob; [ImageFont.truetype(f,40) for f in glob.glob('/home/Darsh/.local/share/fonts/*.ttf')]; print('ALL LOADED', len(glob.glob('/home/Darsh/.local/share/fonts/*.ttf')))"`
    Expected Result: "ALL LOADED" with count >= 20; zero exceptions
    Failure Indicators: Missing files; ImageFont.UnidentifiedImageError on any file
    Evidence: .omo/evidence/task-1-font-inventory.txt

  Scenario: Missing/corrupt font file degrades gracefully (negative)
    Tool: Bash (python3)
    Preconditions: At least one font file removed or a corrupt file dropped in the fonts dir
    Steps:
      1. Simulate failure: `touch /home/Darsh/.local/share/fonts/Corrupt-Test.ttf` (0-byte file, not a real font)
      2. Run `python3 -c "from PIL import ImageFont; ImageFont.truetype('/home/Darsh/.local/share/fonts/Corrupt-Test.ttf', 40)"` and capture the exception type
      3. Assert the exception is catchable (`ImageFont.UnidentifiedImageError` or similar), then `rm /home/Darsh/.local/share/fonts/Corrupt-Test.ttf`
    Expected Result: Exception is raised by Pillow (proving the plan's `_find_font`/registry fallback will need to handle it); corrupt file removed afterward
    Failure Indicators: Pillow silently loads a corrupt file without error (would hide fallback bugs); leftover corrupt file
    Evidence: .omo/evidence/task-1-corrupt-font.txt
  ```

  **Commit**: YES
  - Message: `feat(fonts): download OFL font library`
  - Files: (system dir, not git-tracked — note in commit message)
  - Pre-commit: `ls ~/.local/share/fonts/ | wc -l`

- [ ] 2. Create `fonts.json` manifest with per-font descriptions

  **What to do**:
  - Create `/home/Darsh/codn/fonts.json` with structure:
    ```json
    {
      "serif": {
        "description": "Elegant, editorial. High-contrast strokes; use for luxury/literary moods.",
        "fonts": [
          {
            "name": "Cormorant Garamond",
            "file": "CormorantGaramond-Regular.ttf",
            "bold_file": "CormorantGaramond-Bold.ttf",
            "description": "High-contrast Garamond-inspired serif with delicate hairline strokes; reads as refined, literary, luxurious.",
            "use_cases": "Luxury/fashion quote posts, editorial headlines, romantic literary moods. Use at 28px+ (large/xlarge sizes).",
            "avoid_cases": "Small body text, casual/energetic moods, busy backgrounds with low contrast."
          }
        ]
      }
    }
    ```
  - Categories: `serif`, `sans-serif`, `monospace`, `handwritten`, `display` (keep existing 5)
  - For each category: a 1-2 sentence category description + per-font entries with `name`, `file`, `bold_file` (nullable), `description` (2-3 sentences visual), `use_cases`, `avoid_cases`
  - Include existing user fonts (Playfair Display, Caveat, Dancing Script, Oswald, Pacifico) PLUS the new downloads from Task 1 — every font family that will be referenced
  - Each font description must be VISUAL and ACTIONABLE (what it looks like + when to use + when NOT to) so a blind LLM can choose well
  - Validate JSON parses: `python3 -c "import json; json.load(open('fonts.json'))"`

  **Must NOT do**:
  - Do NOT include fonts not actually downloaded (file must exist or be marked fallback)
  - Do NOT write vague descriptions like "nice elegant font" — must be concrete (stroke contrast, weight, x-height, mood)
  - Do NOT add new categories beyond the existing 5

  **Recommended Agent Profile**:
  - **Category**: `writing`
    - Reason: Requires careful copywriting — visual descriptions and use-case guidance for a blind LLM
  - **Skills**: []
  - **Skills Evaluated but Omitted**:
    - `impeccable`: not a UI task; descriptions are for LLM consumption

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 3, 4)
  - **Blocks**: 3 (registry schema), 5 (prompt text), 7 (tests)
  - **Blocked By**: 1 (needs confirmed file names)

  **References**:
  - `llama_manager.py:195-214` (`_OVERLAY_SYSTEM`) — current prompt; new prompt text will be built from this manifest
  - `llama_manager.py:239-267` (`_normalise_overlay`) — whitelist that must match manifest categories
  - `text_overlay.py:12-37` (`_SYSTEM_FONTS`) — existing font-to-path mapping being replaced by this manifest
  - Websearch data: Bebas Neue (tall all-caps, display-only, pairs with Inter), Great Vibes (formal calligraphy, short bursts only), Cormorant Garamond (high-contrast luxury, 28px+)

  **Acceptance Criteria**:
  - [ ] `fonts.json` parses as valid JSON
  - [ ] Every `file`/`bold_file` referenced exists in `~/.local/share/fonts/` (or is documented as fallback)
  - [ ] All 5 categories present with >= 3 font entries each
  - [ ] Every font entry has non-empty `description`, `use_cases`, `avoid_cases`

  **QA Scenarios**:
  ```
  Scenario: Manifest is valid and complete
    Tool: Bash (python3)
    Preconditions: fonts.json created; Task 1 fonts downloaded
    Steps:
      1. `python3 -c "import json; d=json.load(open('fonts.json')); print('categories:', list(d.keys()))"`
      2. `python3 -c "import json,os; d=json.load(open('fonts.json')); missing=[f['file'] for c in d.values() for f in c['fonts'] if not os.path.exists(os.path.expanduser('~/.local/share/fonts/'+f['file']))]; print('MISSING:', missing)"`
    Expected Result: 5 categories listed; MISSING: [] (empty)
    Failure Indicators: JSON parse error; any missing font file
    Evidence: .omo/evidence/task-2-manifest-valid.txt

  Scenario: Manifest rejects malformed JSON (negative)
    Tool: Bash (python3)
    Preconditions: fonts.json exists
    Steps:
      1. Create a corrupted copy: `python3 -c "open('/tmp/bad_fonts.json','w').write('{not valid json')"`
      2. `python3 -c "import json; json.load(open('/tmp/bad_fonts.json'))"` and capture the exception
      3. Assert `json.JSONDecodeError` is raised (proving registry must handle malformed manifests), remove temp file
    Expected Result: JSONDecodeError raised — confirms the plan's error-handling requirement for FontRegistry
    Failure Indicators: No exception (silent corruption)
    Evidence: .omo/evidence/task-2-malformed-manifest.txt
  ```

  **Commit**: YES
  - Message: `feat(fonts): add fonts.json manifest with descriptions`
  - Files: `fonts.json`
  - Pre-commit: `python3 -c "import json; json.load(open('fonts.json'))"`

- [ ] 3. Create `font_registry.py` (loader, resolver, prompt-builder, validator)

  **What to do**:
  - Create `/home/Darsh/codn/font_registry.py` with a `FontRegistry` class:
    - `__init__(self, manifest_path="fonts.json")` — loads manifest, builds lookup structures; raises clear error if manifest missing/invalid
    - `categories()` → list of the 5 category names
    - `fonts_in(category)` → list of font dicts
    - `resolve(style, font_name, weight="regular")` → absolute font file path; falls back: unknown name → first font in style; unknown weight/bold missing → regular file; unknown style → serif default; missing file → walk style list for first existing file; ultimately None
    - `build_prompt_text(max_chars=2000)` → condensed string for the LLM system prompt: per-category `description` + font `name` + short `description` + `use_cases`. Truncates gracefully at `max_chars`
    - `validate(style_dict)` → given a full overlay style dict, return corrected dict (used by llama_manager `_normalise_overlay`)
  - Handle manifest path relative to BASE_DIR (`config.py:30`)
  - Log resolution fallbacks with `print` (matches existing codebase logging style)

  **Must NOT do**:
  - Do NOT depend on llama_manager or text_overlay (must be importable standalone — prevents circular imports)
  - Do NOT load fonts at import time eagerly if it causes overhead — lazy-load on first use
  - Do NOT raise exceptions on missing fonts — always fall back

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Core abstraction with fallback chains, multiple edge cases, and a prompt-builder with truncation — needs careful design
  - **Skills**: []
  - **Skills Evaluated but Omitted**:
    - `git-master`: no git ops

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 4)
  - **Blocks**: 5 (prompt builder), 6 (renderer resolver), 7 (tests)
  - **Blocked By**: 1 (fallback file list), 2 (manifest schema)

  **References**:
  - `config.py:30` (`BASE_DIR`) — manifest path resolution pattern
  - `text_overlay.py:40-54` (`_find_font`) — existing fallback chain logic to mirror in `resolve()`
  - `llama_manager.py:237-267` (`_normalise_overlay`) — existing whitelist validation; `validate()` will replace this
  - `fonts.json` (Task 2) — data schema the registry loads

  **Acceptance Criteria**:
  - [ ] `python3 -c "from font_registry import FontRegistry; r=FontRegistry(); print(r.categories())"` → 5 categories
  - [ ] `resolve("serif", "Nonexistent Font", "regular")` returns a valid existing path (fallback works)
  - [ ] `resolve("serif", "Cormorant Garamond", "bold")` returns bold file if present, else regular
  - [ ] `build_prompt_text()` returns string < 2000 chars containing font names
  - [ ] `validate()` accepts a v2 dict and returns it unchanged when valid; corrects invalid values

  **QA Scenarios**:
  ```
  Scenario: Fallback chain works for unknown font
    Tool: Bash (python3)
    Preconditions: font_registry.py + fonts.json exist
    Steps:
      1. `python3 -c "from font_registry import FontRegistry; r=FontRegistry(); p=r.resolve('serif','Totally Fake Font','regular'); import os; print('RESOLVED:', p, '| EXISTS:', os.path.exists(p))"`
    Expected Result: RESOLVED points to an existing serif font path; EXISTS: True
    Failure Indicators: Exception raised; None returned; non-existent path
    Evidence: .omo/evidence/task-3-resolve-fallback.txt

  Scenario: Prompt text fits context budget
    Tool: Bash (python3)
    Preconditions: same
    Steps:
      1. `python3 -c "from font_registry import FontRegistry; r=FontRegistry(); t=r.build_prompt_text(max_chars=2000); print('LEN:', len(t)); print('Cormorant' in t)"`
    Expected Result: LEN <= 2000; True (font names present in prompt text)
    Failure Indicators: LEN > 2000; font names missing
    Evidence: .omo/evidence/task-3-prompt-text.txt
  ```

  **Commit**: YES
  - Message: `feat(fonts): add font_registry loader/resolver/validator`
  - Files: `font_registry.py`
  - Pre-commit: `python3 -c "from font_registry import FontRegistry; FontRegistry()"`

- [ ] 4. Set up pytest infrastructure

  **What to do**:
  - Add `pytest>=8.0` to `/home/Darsh/codn/requirements.txt`
  - Create `/home/Darsh/codn/tests/__init__.py` and `/home/Darsh/codn/tests/conftest.py`
  - In `conftest.py`: add project root to `sys.path` (so `import text_overlay` etc. work), optionally a fixture creating a small test image with Pillow for renderer tests
  - Create `/home/Darsh/codn/pytest.ini` (or pyproject section) with `testpaths = tests`
  - Verify: `pytest tests/ -v` runs (collects 0 tests initially — expected) without import errors

  **Must NOT do**:
  - Do NOT install new test frameworks beyond pytest (pytest-asyncio already present if needed)
  - Do NOT test against the live llama-server or ComfyUI (no GPU/network in tests)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Standard, well-documented setup task
  - **Skills**: []
  - **Skills Evaluated but Omitted**:
    - `git-master`: no git ops

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 3)
  - **Blocks**: 7, 8 (test execution), 9 (integration uses pytest)
  - **Blocked By**: None

  **References**:
  - `requirements.txt` — file to update
  - `config.py:30` (`BASE_DIR`) — path pattern for conftest sys.path
  - Existing modules to be tested: `text_overlay.py`, `llama_manager.py`, `font_registry.py`

  **Acceptance Criteria**:
  - [ ] `pytest tests/ -v` exits 0 and collects tests without import errors
  - [ ] `requirements.txt` contains `pytest>=8.0`
  - [ ] `tests/conftest.py` imports `text_overlay` successfully

  **QA Scenarios**:
  ```
  Scenario: pytest collects and imports cleanly
    Tool: Bash
    Preconditions: pytest installed; tests/ created
    Steps:
      1. `cd /home/Darsh/codn && pytest tests/ -v 2>&1 | tail -20`
    Expected Result: exit code 0; "no tests ran" or collected count; NO ImportError
    Failure Indicators: ImportError; collection errors; exit != 0
    Evidence: .omo/evidence/task-4-pytest-collect.txt

  Scenario: Import failure surfaces clearly (negative)
    Tool: Bash
    Preconditions: pytest infra exists
    Steps:
      1. `cd /home/Darsh/codn && python3 -c "import text_overlay"` — confirm import succeeds
      2. Temporarily break an import to prove tests would fail loudly: create a test file with `import nonexistent_module_xyz`, run `pytest tests/ -q 2>&1 | tail -5`
      3. Assert exit code != 0 and error names the missing module; remove the broken test file
    Expected Result: pytest fails loudly with ModuleNotFoundError — proving CI/test gating works
    Failure Indicators: pytest passes despite broken import
    Evidence: .omo/evidence/task-4-import-failure.txt
  ```

  **Commit**: YES
  - Message: `chore(test): add pytest infrastructure`
  - Files: `requirements.txt`, `tests/__init__.py`, `tests/conftest.py`, `pytest.ini`
  - Pre-commit: `pytest tests/ -q`

- [ ] 5. Extend `llama_manager.py` overlay contract v2 (prompt + whitelist)

  **What to do**:
  - Update `_OVERLAY_SYSTEM` (llama_manager.py:195-214) to become a function `build_overlay_system_prompt()` that:
    - Calls `FontRegistry().build_prompt_text()` and injects the condensed font descriptions into the system prompt so the LLM can read what each font looks like
    - Adds new JSON fields to the output contract:
      - `"font": specific font name from the manifest (e.g. "Cormorant Garamond")`
      - `"font_weight": "regular" | "bold"`
      - `"position": one of: "bottom", "bottom-left", "bottom-right", "top", "top-left", "top-right", "center", "middle-left", "middle-right"`
      - `"x_offset": signed percentage (e.g. -15 to 15) — fine-tune horizontal placement from the named anchor`
      - `"y_offset": signed percentage (e.g. -15 to 15) — fine-tune vertical placement from the named anchor`
      - `"letter_spacing": pixels, 0 to 10 (0 = normal)`
      - `"text_effect": one of: "none", "shadow", "outline", "glow"`
      - `"glow_color": hex color (used when text_effect is "glow")`
      - `"text_gradient_from" / "text_gradient_to": hex colors (used for gradient fill)`
    - Keep existing fields unchanged (quote, author, alignment, text_color, font_size, background_overlay)
    - Keep prompt guidance: prefer subtle effects, medium font size, avoid the art's focal subject
  - Update `_normalise_overlay` (llama_manager.py:237-267):
    - Add new allowed sets: expanded positions, effects incl. "glow", font_weight
    - Add numeric bounds: `x_offset`/`y_offset` float -100..100 (default 0), `letter_spacing` int 0..10 (default 0)
    - Validate `font` against `FontRegistry` (unknown → style default name), `glow_color`/`text_gradient_from`/`text_gradient_to` via `_is_hex_color`
    - Default `text_effect` stays "shadow"; add defaults for all new fields
  - Keep `_parse_overlay_response` (llama_manager.py:217-234) unchanged (it already routes through `_normalise_overlay`)
  - Update the `generate_text_overlay` docstring to document v2 fields

  **Must NOT do**:
  - Do NOT change alignment/background_overlay/font_size allowed values or defaults
  - Do NOT remove "none"/"shadow"/"outline" from text_effect
  - Do NOT change the API surface of `generate_text_overlay` (signature stays)
  - Do NOT import text_overlay (keep layering: llama_manager → font_registry only)

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Prompt engineering + validation whitelist changes with backward-compat constraints
  - **Skills**: []
  - **Skills Evaluated but Omitted**:
    - `git-master`: no git ops

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Task 6)
  - **Blocks**: 7 (normalisation tests), 9 (integration)
  - **Blocked By**: 2 (manifest), 3 (registry prompt-builder/validate)

  **References**:
  - `llama_manager.py:195-214` (`_OVERLAY_SYSTEM`) — prompt to convert to function with injected font descriptions
  - `llama_manager.py:237-267` (`_normalise_overlay`) — whitelist + defaults to extend
  - `llama_manager.py:270-273` (`_is_hex_color`) — reuse for new color fields
  - `font_registry.py` (Task 3) — `build_prompt_text()` and `validate()` to call
  - `fonts.json` (Task 2) — font names the LLM will reference

  **Acceptance Criteria**:
  - [ ] `build_overlay_system_prompt()` returns a string containing font descriptions and all new field names
  - [ ] `_normalise_overlay({})` returns dict with all v2 defaults, no exceptions
  - [ ] `_normalise_overlay` with `text_effect: "glow"` and valid `glow_color` keeps both
  - [ ] `_normalise_overlay` with `position: "bottom-left"` keeps it; with `position: "somewhere-weird"` falls back to "bottom"
  - [ ] `_normalise_overlay` with `x_offset: 500` clamps to 100 (or 0 default on invalid)
  - [ ] `generate_text_overlay` signature unchanged

  **QA Scenarios**:
  ```
  Scenario: Normalisation handles v2 fields and rejects invalid
    Tool: Bash (python3)
    Preconditions: llama_manager.py updated; font_registry.py present
    Steps:
      1. `python3 -c "import llama_manager; d=llama_manager._normalise_overlay({'quote':'Test.','font':'Bogus','text_effect':'glow','glow_color':'#FF00FF','position':'bottom-left','x_offset':500,'letter_spacing':99}); import json; print(json.dumps(d, indent=2))"`
    Expected Result: font falls back to a real serif name; glow kept; glow_color kept; position bottom-left; x_offset <= 100; letter_spacing <= 10
    Failure Indicators: Exception; unclamped x_offset=500; kept bogus font
    Evidence: .omo/evidence/task-5-normalise-v2.txt

  Scenario: System prompt includes font descriptions
    Tool: Bash (python3)
    Preconditions: same
    Steps:
      1. `python3 -c "import llama_manager; p=llama_manager.build_overlay_system_prompt(); print('LEN', len(p)); print('font_weight' in p and 'glow_color' in p and 'Cormorant' in p)"`
    Expected Result: LEN sane (< 4096); True (new fields + font names present)
    Failure Indicators: prompt too long; missing fields/font names
    Evidence: .omo/evidence/task-5-prompt-built.txt
  ```

  **Commit**: YES
  - Message: `feat(overlay): extend LLM overlay contract with font/position/effect options`
  - Files: `llama_manager.py`
  - Pre-commit: `python3 -c "import llama_manager; llama_manager._normalise_overlay({})"`

- [ ] 6. Extend `text_overlay.py` renderer v2 (bold, positions+offsets, glow, gradient, letter-spacing)

  **What to do**:
  - Replace `_SYSTEM_FONTS` dict usage with `FontRegistry` in `_find_font` (text_overlay.py:40-54):
    - New signature: `_find_font(style, font_name, weight)` → delegates to `FontRegistry().resolve(style, font_name, weight)`
    - Keep the final fallback to any system font if registry returns None
  - In `add_text_overlay` (text_overlay.py:88-198):
    - Read new style keys with `.get()` defaults: `font`, `font_weight` ("regular"), `position` (now 9 values), `x_offset`/`y_offset` (0.0), `letter_spacing` (0), `glow_color` ("#FFD700"), `text_gradient_from`/`text_gradient_to`
    - Pass `font_weight` to `_find_font`; use bold file when requested and available
    - Position: map named anchors to x/y base (expand the existing if/elif at text_overlay.py:157-164 to all 9 positions), then apply `x_offset`/`y_offset` as percentages of image width/height, clamped so text stays within margins (min margin_x, max w - margin_x - text width, etc.)
    - Letter-spacing: when `letter_spacing > 0`, draw text character-by-character, advancing by `textlength(char) + letter_spacing` (measure with `draw.textlength`); must re-run `_wrap_text` width checks with spacing accounted (spacing adds `(len(line)-1) * letter_spacing`)
    - Author line inherits font weight + effect, but NOT letter-spacing by default (keep simple)
  - Extend `_draw_text_with_effect` (text_overlay.py:201-222):
    - Add `glow` case: draw text multiple times offset in 8 directions with `glow_color` + blur (use `ImageFilter.GaussianBlur` on a temp layer or draw N offsets), then draw main text on top
  - Add `_draw_gradient_text(draw, xy, text, font, from_hex, to_hex, effect)` helper:
    - Render text as mask, create vertical gradient image between the two colors, composite via mask (standard Pillow gradient-text technique)
    - Applies when `text_gradient_from` and `text_gradient_to` are both set (overrides plain `text_color`); falls back to solid color if either invalid
  - Update module docstring + `add_text_overlay` docstring to document v2 keys

  **Must NOT do**:
  - Do NOT change alignment handling, background overlay (`_draw_bg_overlay`), or font-size scaling (`_size_to_pixels`)
  - Do NOT change the function signature `add_text_overlay(image_path, output_path, style)` or return value
  - Do NOT import llama_manager (layering: text_overlay → font_registry only)
  - Do NOT add new dependencies beyond Pillow (already present)

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Rendering math (offsets, letter-spacing measuring, gradient masks, glow blur) with backward compat — highest-complexity task
  - **Skills**: []
  - **Skills Evaluated but Omitted**:
    - `impeccable`: this is rendering logic, not UI design; visual QA happens in Task 9/F3
    - `git-master`: no git ops

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Task 5)
  - **Blocks**: 8 (renderer tests), 9 (integration)
  - **Blocked By**: 1 (fonts), 3 (registry resolver)

  **References**:
  - `text_overlay.py:88-198` (`add_text_overlay`) — main render path to extend
  - `text_overlay.py:40-54` (`_find_font`) — font resolution to delegate to registry
  - `text_overlay.py:201-222` (`_draw_text_with_effect`) — effect dispatch to extend with glow
  - `text_overlay.py:253-263` (`_hex_to_rgba`) — reuse for gradient colors
  - `font_registry.py` (Task 3) — `resolve(style, name, weight)`
  - Pillow docs: `ImageDraw.textlength`, `ImageFilter.GaussianBlur`, `Image.alpha_composite` (gradient text technique)

  **Acceptance Criteria**:
  - [ ] `add_text_overlay` accepts a v2 style dict and produces an output image without error
  - [ ] A style dict with ONLY old keys (quote, author, font_style, alignment, text_color, position, font_size, background_overlay, text_effect) renders identically to before (backward compat)
  - [ ] `text_effect: "glow"` + `glow_color` produces a visibly different image than "shadow"
  - [ ] Gradient text (`text_gradient_from`/`text_gradient_to` set) renders without error
  - [ ] `letter_spacing: 6` produces a wider text block than `letter_spacing: 0` (measured via image width)
  - [ ] `position: "bottom-left"` + `x_offset: 10` places text differently than plain "bottom-left"
  - [ ] Text never goes off-image even with extreme offsets (clamped)

  **QA Scenarios**:
  ```
  Scenario: v2 render produces valid output for every new option
    Tool: Bash (python3)
    Preconditions: a base art image exists (create one with Pillow if not)
    Steps:
      1. `python3 -c "
  import text_overlay
  from PIL import Image
  Image.new('RGB', (1024,1024), (30,40,60)).save('/tmp/base.png')
  styles = [
    {'quote':'Glow test quote.','text_effect':'glow','glow_color':'#FF00FF','font_size':'large'},
    {'quote':'Gradient test.','text_gradient_from':'#FFD700','text_gradient_to':'#FF4500','font_size':'large'},
    {'quote':'Spaced letters.','letter_spacing':6,'font_size':'large'},
    {'quote':'Precise pos.','position':'bottom-left','x_offset':8,'y_offset':-5,'font_size':'medium'},
    {'quote':'Bold weight.','font_weight':'bold','font_size':'large'},
  ]
  for i,s in enumerate(styles):
      out=f'/tmp/v2_{i}.jpg'; text_overlay.add_text_overlay('/tmp/base.png', out, s); print('OK', i, out)
  "`"
    Expected Result: 5 "OK" lines; 5 jpg files exist; zero exceptions
    Failure Indicators: Any exception; missing output files
    Evidence: .omo/evidence/task-6-v2-renders.txt (plus the 5 jpg files referenced)

  Scenario: Backward compat — old-style dict renders
    Tool: Bash (python3)
    Preconditions: same base image
    Steps:
      1. `python3 -c "
  import text_overlay
  old={'quote':'Old style quote.','author':'','font_style':'serif','alignment':'center','text_color':'#FFFFFF','position':'bottom','font_size':'medium','background_overlay':'dark-bottom','text_effect':'shadow'}
  text_overlay.add_text_overlay('/tmp/base.png','/tmp/old_style.jpg', old); print('OLD OK')
  "`"
    Expected Result: "OLD OK"; file exists; no error
    Failure Indicators: KeyError/exception on old keys
    Evidence: .omo/evidence/task-6-backward-compat.txt
  ```

  **Commit**: YES
  - Message: `feat(overlay): renderer v2 - bold, offsets, glow, gradient, letter-spacing`
  - Files: `text_overlay.py`
  - Pre-commit: `python3 -c "import text_overlay; print('import ok')"`

- [ ] 7. Unit tests: `font_registry` + `llama_manager` normalisation

  **What to do**:
  - Create `tests/test_font_registry.py`:
    - `test_categories_returns_five`: `FontRegistry().categories()` == 5 categories
    - `test_resolve_known_font`: `resolve("serif", "Cormorant Garamond", "regular")` → path that exists
    - `test_resolve_unknown_name_falls_back`: unknown name → still returns existing path in that style
    - `test_resolve_unknown_style_falls_back`: bogus style → serif default, existing path
    - `test_resolve_bold_missing_falls_back`: weight "bold" with no bold_file → regular file
    - `test_resolve_missing_file_falls_back`: manifest references missing file → next existing file, else None (no exception)
    - `test_build_prompt_text_length`: `build_prompt_text(max_chars=2000)` length <= 2000 and contains >= 3 font names
    - `test_build_prompt_text_categories`: output contains all 5 category names
  - Create `tests/test_llama_manager.py`:
    - `test_normalise_empty_dict_defaults`: `_normalise_overlay({})` → all v2 defaults, no exception
    - `test_normalise_keeps_valid_v2`: font + glow + glow_color + position bottom-left + offsets + letter_spacing survive
    - `test_normalise_clamps_offsets`: x_offset=500 clamped; letter_spacing=99 clamped to 10
    - `test_normalise_invalid_effect`: "laser" → valid default
    - `test_normalise_invalid_position`: "weird" → "bottom"
    - `test_normalise_unknown_font`: "Bogus" → real font name in same category
    - `test_normalise_hex_color_validation`: invalid glow_color/gradient hex → defaults
    - `test_prompt_contains_new_fields`: `build_overlay_system_prompt()` contains "font_weight", "glow_color", "x_offset", "letter_spacing", "text_gradient_from"
    - `test_parse_overlay_response_valid_json`: wrapped JSON string parses to normalised dict
  - No network/GPU/llama-server calls in any test

  **Must NOT do**:
  - Do NOT spin up llama-server or make network calls in tests
  - Do NOT test text_overlay rendering here (that's Task 8)
  - Do NOT test `/generate` API (needs GPU/llama — integration QA handles this)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Straightforward pure-function unit tests with clear cases
  - **Skills**: []
  - **Skills Evaluated but Omitted**:
    - `git-master`: no git ops

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 8, 9)
  - **Blocks**: 9 (integration depends on passing tests)
  - **Blocked By**: 3 (registry), 4 (pytest infra), 5 (normalise v2)

  **References**:
  - `font_registry.py` (Task 3) — class under test
  - `llama_manager.py:237-267` (`_normalise_overlay`), `llama_manager.py:270-273` (`_is_hex_color`), `llama_manager.py:217-234` (`_parse_overlay_response`), new `build_overlay_system_prompt` (Task 5)
  - `tests/conftest.py` (Task 4) — sys.path fixture
  - `fonts.json` (Task 2) — font names used in assertions

  **Acceptance Criteria**:
  - [ ] `pytest tests/test_font_registry.py tests/test_llama_manager.py -v` → all pass (>= 15 tests)
  - [ ] No test requires network/GPU/llama-server

  **QA Scenarios**:
  ```
  Scenario: Registry + normalisation tests pass
    Tool: Bash
    Preconditions: tests written; pytest infra from Task 4
    Steps:
      1. `cd /home/Darsh/codn && pytest tests/test_font_registry.py tests/test_llama_manager.py -v 2>&1 | tail -30`
    Expected Result: "N passed" (N >= 15), 0 failed
    Failure Indicators: Any failure; collection error; hanging test
    Evidence: .omo/evidence/task-7-unit-tests.txt

  Scenario: Malformed LLM output handled without crash (negative)
    Tool: Bash (python3)
    Preconditions: llama_manager v2 from Task 5
    Steps:
      1. Feed garbage: `python3 -c "import llama_manager; d=llama_manager._normalise_overlay({'quote':None,'font':None,'text_effect':None,'position':None}); print('OK', d['quote'][:20])"`
      2. Feed non-dict fallback path: call `_parse_overlay_response('not json at all')` and confirm it returns a normalised dict
    Expected Result: Both return valid normalised dicts with defaults — no crash on None values or unparseable text
    Failure Indicators: TypeError/AttributeError on None; unhandled exception
    Evidence: .omo/evidence/task-7-malformed-input.txt
  ```

  **Commit**: YES
  - Message: `test(overlay): unit tests for registry and llama_manager parsing`
  - Files: `tests/test_font_registry.py`, `tests/test_llama_manager.py`
  - Pre-commit: `pytest tests/test_font_registry.py tests/test_llama_manager.py -q`

- [ ] 8. Unit tests: `text_overlay` renderer

  **What to do**:
  - Create `tests/test_text_overlay.py` with a small Pillow `base_image` fixture (e.g. 400x400 solid color) in conftest or in-file:
    - `test_add_overlay_v2_glow`: text_effect "glow" + glow_color renders to output path, file exists
    - `test_add_overlay_v2_gradient`: gradient from/to renders, file exists
    - `test_add_overlay_v2_letter_spacing`: same quote at letter_spacing 0 vs 6 → text block wider (compare bounding boxes)
    - `test_add_overlay_v2_offsets`: position "bottom-left" x_offset 0 vs +8 → x positions differ (bounding box scan)
    - `test_add_overlay_v2_bold`: font_weight "bold" with font that has bold_file → renders without error
    - `test_add_overlay_backward_compat`: OLD-style dict (only pre-v2 keys) renders without error — regression guard
    - `test_add_overlay_empty_style_defaults`: `add_text_overlay(img, out, {})` → renders fallback quote, no exception
    - `test_add_overlay_offsets_clamped`: extreme x_offset/y_offset (500/-500) → text stays within image bounds (assert corner pixels remain background color)
    - `test_wrap_text_respects_letter_spacing`: `_wrap_text` lines stay within max_width when spacing > 0
  - Use a helper finding the leftmost/rightmost non-background pixel column (deterministic solid background makes this reliable)

  **Must NOT do**:
  - Do NOT require network, GPU, or llama-server
  - Do NOT assert on exact pixel aesthetics (fonts differ across systems) — assert structural properties (file exists, width differences, in-bounds)
  - Do NOT depend on specific font files beyond what the registry falls back to

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Pixel-level assertions (bounds, width differences, position changes) require careful test design
  - **Skills**: []
  - **Skills Evaluated but Omitted**:
    - `visual-qa`/`impeccable`: unit tests assert structure, not aesthetics; visual judgment happens in Task 9/F3

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 7, 9)
  - **Blocks**: 9 (integration)
  - **Blocked By**: 3 (registry), 4 (pytest infra), 6 (renderer v2)

  **References**:
  - `text_overlay.py:88-198` (`add_text_overlay`) — function under test
  - `text_overlay.py:70-85` (`_wrap_text`) — wrapping logic to verify with spacing
  - `text_overlay.py:201-222` (`_draw_text_with_effect`) — glow path
  - `tests/conftest.py` (Task 4) — fixture conventions
  - Pillow `ImageDraw.textlength` — measurement used in assertions

  **Acceptance Criteria**:
  - [ ] `pytest tests/test_text_overlay.py -v` → all pass (>= 8 tests)
  - [ ] Backward-compat regression test present and passing
  - [ ] Offset-clamp test proves text stays in-bounds at extreme offsets

  **QA Scenarios**:
  ```
  Scenario: Renderer tests pass
    Tool: Bash
    Preconditions: tests written; renderer v2 from Task 6
    Steps:
      1. `cd /home/Darsh/codn && pytest tests/test_text_overlay.py -v 2>&1 | tail -30`
    Expected Result: "N passed" (N >= 8), 0 failed
    Failure Indicators: Any failure (esp. backward-compat or clamp test)
    Evidence: .omo/evidence/task-8-renderer-tests.txt

  Scenario: Invalid style values don't crash the renderer (negative)
    Tool: Bash (python3)
    Preconditions: renderer v2 from Task 6; base image from Task 6 QA
    Steps:
      1. `python3 -c "
  import text_overlay
  bad = {'quote':'X','text_color':'notacolor','glow_color':'zzz','text_gradient_from':'#12345','font_weight':'superbold','position':'underground','letter_spacing':-50,'x_offset':'abc'}
  text_overlay.add_text_overlay('/tmp/base.png','/tmp/bad_style.jpg', bad); print('BAD STYLE OK')
  "`
    Expected Result: "BAD STYLE OK" — renderer survives every invalid value via normalise/fallbacks
    Failure Indicators: Exception on any invalid field
    Evidence: .omo/evidence/task-8-invalid-styles.txt
  ```

  **Commit**: YES
  - Message: `test(overlay): unit tests for text_overlay renderer`
  - Files: `tests/test_text_overlay.py`
  - Pre-commit: `pytest tests/test_text_overlay.py -q`

- [ ] 9. Integration QA — render sample images for every new option

  **What to do**:
  - Create `/home/Darsh/codn/output/style_samples/` and generate one 1024x1024 base image (solid gradient background created with Pillow — no ComfyUI needed)
  - Build a script `output/style_samples/generate_samples.py` (temporary, may be removed after) that renders:
    - 1 image per text_effect: none, shadow, outline, glow (glow with a distinct glow_color, e.g. cyan)
    - 1 image per new position + offset combo: bottom-left+x, bottom-right+y, top-left, top-right, upper-center (position center + y_offset negative), middle-left+x
    - 1 image gradient fill, 1 image letter-spacing wide, 1 image bold weight
    - 1 backward-compat image (old-style dict)
    - 1 image with extreme offsets (x_offset 40, y_offset 40) to eyeball clamping
  - Save all to `output/style_samples/` with descriptive filenames (`effect_glow.jpg`, `pos_bottomleft_x8.jpg`, ...)
  - Run the script; verify all files produced
  - Smoke-check the LLM side WITHOUT GPU: import `llama_manager`, print `build_overlay_system_prompt()` length and confirm it contains font names + new fields
  - Optionally smoke-test `/health` if the server is running (do NOT start llama-server/ComfyUI)

  **Must NOT do**:
  - Do NOT start llama-server, ComfyUI, or upload to Instagram
  - Do NOT call `/generate` (requires GPU models)
  - Do NOT leave heavy artifacts in the repo (sample script lives in output/, git-ignored)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Broad verification sweep across all new features with evidence capture
  - **Skills**: []
  - **Skills Evaluated but Omitted**:
    - `playwright`: no browser needed
    - `visual-qa`: rendering is deterministic Pillow output; structural QA in tests already covers it

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 7, 8)
  - **Blocks**: F1-F4 (needs evidence)
  - **Blocked By**: 5 (prompt v2), 6 (renderer v2), 7 (unit tests), 8 (unit tests)

  **References**:
  - `output/` — existing output dir convention (config.py:31)
  - `text_overlay.py:88-198` — renderer to exercise
  - `llama_manager.py` — `build_overlay_system_prompt()` to smoke-check
  - Task 6 QA scenarios — reuse the style dicts already proven to work

  **Acceptance Criteria**:
  - [ ] `output/style_samples/` contains >= 10 rendered jpg files covering: all 4 effects, >= 5 position/offset combos, gradient, letter-spacing, bold, backward-compat, extreme offset
  - [ ] Every file opens with `Image.open(...).verify()` without error
  - [ ] Script exit code 0

  **QA Scenarios**:
  ```
  Scenario: All style samples rendered and valid
    Tool: Bash (python3)
    Preconditions: renderer v2 working (Task 6)
    Steps:
      1. `cd /home/Darsh/codn && python3 output/style_samples/generate_samples.py`
      2. `python3 -c "from PIL import Image; import glob; files=glob.glob('output/style_samples/*.jpg'); [Image.open(f).verify() for f in files]; print('VALID', len(files))"`
    Expected Result: "VALID" with count >= 10
    Failure Indicators: Exception during render; invalid/corrupt jpg; count < 10
    Evidence: .omo/evidence/task-9-samples-valid.txt

  Scenario: LLM prompt smoke-check without GPU
    Tool: Bash (python3)
    Preconditions: llama_manager v2 from Task 5
    Steps:
      1. `python3 -c "import llama_manager; p=llama_manager.build_overlay_system_prompt(); print('LEN', len(p)); print('font_weight' in p, 'x_offset' in p, 'Cormorant' in p or 'Playfair' in p)"`
    Expected Result: LEN < 4096; True True True (new fields + real font names present)
    Failure Indicators: Missing fields; prompt > 4096 chars; import error
    Evidence: .omo/evidence/task-9-prompt-smoke.txt
  ```

  **Commit**: YES
  - Message: `test(overlay): integration QA evidence`
  - Files: `.omo/evidence/` (output/ is git-ignored)
  - Pre-commit: `python3 -c "from PIL import Image; import glob; files=glob.glob('output/style_samples/*.jpg'); print(len(files))"`

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.
>
> **Do NOT auto-proceed after verification. Wait for user's explicit approval before marking work complete.**

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read file, run pytest, render sample). For each "Must NOT Have": search codebase for forbidden patterns — reject with file:line if found. Check evidence files exist in `.omo/evidence/`. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run `python -m py_compile` on all changed files + `pytest tests/`. Review changed files for: `except: pass` swallowing errors, unused imports, commented-out code, over-abstraction, generic names. Check font descriptions are factual and non-repetitive.
  Output: `Compile [PASS/FAIL] | Tests [N pass/N fail] | Files [N clean/N issues] | VERDICT`

- [ ] F3. **Real Manual QA** — `unspecified-high`
  Start from clean state. Execute EVERY QA scenario from EVERY task — follow exact steps, capture evidence. Test cross-task integration (fonts.json → registry → prompt → render). Test edge cases: unknown font name, invalid hex colors, extreme offsets, letter-spacing on long quotes. Save to `.omo/evidence/final-qa/`.
  Output: `Scenarios [N/N pass] | Integration [N/N] | Edge Cases [N tested] | VERDICT`

- [ ] F4. **Scope Fidelity Check** — `deep`
  For each task: read "What to do", read actual diff (git diff). Verify 1:1 — everything in spec was built (no missing), nothing beyond spec was built (no creep). Check "Must NOT do" compliance — especially NO alignment/background/font-size changes, NO main.py changes. Detect cross-task contamination.
  Output: `Tasks [N/N compliant] | Contamination [CLEAN/N issues] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

- **1**: `feat(fonts): download OFL font library` - ~/.local/share/fonts (note: system dir, git-ignored)
- **2**: `feat(fonts): add fonts.json manifest with descriptions` - fonts.json
- **3**: `feat(fonts): add font_registry loader/resolver/validator` - font_registry.py
- **4**: `chore(test): add pytest infrastructure` - requirements.txt, tests/, conftest.py
- **5**: `feat(overlay): extend LLM overlay contract with font/position/effect options` - llama_manager.py
- **6**: `feat(overlay): renderer v2 - bold, offsets, glow, gradient, letter-spacing` - text_overlay.py
- **7**: `test(overlay): unit tests for registry and llama_manager parsing` - tests/
- **8**: `test(overlay): unit tests for text_overlay renderer` - tests/
- **9**: `test(overlay): integration QA evidence` - .omo/evidence/

---

## Success Criteria

### Verification Commands
```bash
cd /home/Darsh/codn
pytest tests/ -v        # Expected: all tests pass (N passed)
python3 -c "from font_registry import FontRegistry; r=FontRegistry(); print(r.resolve('serif','Cormorant Garamond','regular'))"  # Expected: valid font path
python3 -c "import text_overlay; print(text_overlay.add_text_overlay.__doc__ is not None)"  # Expected: True
ls ~/.local/share/fonts/  # Expected: includes new fonts (Cormorant, Bebas, GreatVibes, etc.)
```

### Final Checklist
- [ ] All "Must Have" present
- [ ] All "Must NOT Have" absent
- [ ] All tests pass
- [ ] Evidence files exist for every QA scenario
- [ ] User explicitly approved F1-F4 results
