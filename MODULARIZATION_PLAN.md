# Refactoring & Modularization Plan: tvcli floatdash

This document outlines the roadmap for modularizing the FastAPI dashboard backend, decoupling the giant inline HTML template from the Python logic, and restructuring the codebase into clean, maintainable router modules.

---

## Phase 1: Decouple Frontend Assets (HTML Template Extraction)
The inline HTML/JS/CSS in `src/tvcli/floatdash/app.py` spans over 2,400 lines, making it extremely difficult to maintain.

### Tasks
1. **Create templates directory**: Create `src/tvcli/floatdash/templates/`.
2. **Extract HTML content**: Copy the raw HTML template string into `src/tvcli/floatdash/templates/index.html`.
3. **Read template dynamically**: Update the `/` root route inside `app.py` to read `index.html` from the file system. Use a cached file reader helper to avoid disk I/O overhead in production.

---

## Phase 2: Refactor API Router Modules
Break the monolithic `app.py` into a package structure with localized sub-routers under `src/tvcli/floatdash/routers/`.

### Sub-Routers Design
1. **`routers/settings.py`**:
   - Handles `/api/settings`, `/api/settings/update`, and `/api/settings/test`.
   - Uses `SettingsUpdate` and `SettingsTest` Pydantic models.
2. **`routers/alerts.py`**:
   - Handles `/api/alerts/history` logs queries and filters.
3. **`routers/market.py`**:
   - Handles `/api/market`, `/api/market/sectors`, `/api/market/sync-status`, and `/api/market/sync` execution.
4. **`routers/symbol.py`**:
   - Handles `/api/symbol/{code}` detail reports and `/api/symbol/{code}/kap` disclosures.
5. **`routers/images.py`**:
   - Serves dynamically cached charts `/img/market.png` and `/img/symbol/{code}.png`.

---

## Phase 3: Setup Dependencies & Shared Store State
Avoid passing the `store` database reference directly through factory methods. Instead, leverage FastAPI's dependency injection system.

### Tasks
1. **`dependencies.py`**:
   - Implement `get_store()` to yield the active database `ArchiveStore` reference.
   - Implement `get_config()` to load configuration.
2. **Inject dependencies**:
   - Update router endpoints to retrieve resources via `Depends(get_store)`.

---

## Phase 4: Integration & Verification
1. **Reconstruct `app.py`**:
   - Clean up the file to only handle app creation (`create_app`), middleware registration (GZip, exception handlers), static routes, and inclusion of the modular routers.
2. **Format and lint**:
   - Run `just fmt && just lint` to verify coding standards and strict type safety.
3. **Verify tests**:
   - Run the entire test suite `pytest tests/unit/` to ensure full parity and zero regressions.
