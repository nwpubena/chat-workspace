"""Read-only browser smoke test; no extraction, login or cloud calls."""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1440, 'height': 1050}, device_scale_factor=1)
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
    page.goto('http://127.0.0.1:8765/', wait_until='networkidle')
    page.locator('#stat-cards .card').first.wait_for()
    assert page.locator('#stat-cards .stat-number').count() == 2
    page.screenshot(path=str(Path(__file__).with_name('overview.png')), full_page=True)
    page.locator('[data-page="wechat"]').click()
    assert page.locator('#wechat-steps button').count() == 8
    page.locator('[data-page="douyin"]').click()
    assert page.locator('#douyin-steps button').count() == 6
    page.locator('[data-page="search"]').click()
    page.locator('#query').fill('吃饭')
    page.locator('#search-form button[type="submit"]').click()
    page.locator('.result').first.wait_for(timeout=15000)
    assert page.locator('#results .result-meta').count() > 0
    page.locator('#search-mode').select_option('semantic')
    assert page.locator('#sender').is_disabled()
    page.locator('[data-page="reports"]').click()
    page.locator('#build-report').click()
    page.locator('.bar-chart').wait_for()
    assert page.locator('#download-report').is_enabled()
    page.locator('[data-page="model"]').click()
    assert page.locator('#api-key').input_value() == ''
    page.locator('[data-page="tasks"]').click()
    page.locator('[data-page="overview"]').click()
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
    page.screenshot(path=str(Path(__file__).with_name('mobile.png')), full_page=True)
    browser.close()
    print(json.dumps({'passed': not errors, 'browser_errors': errors, 'checks': ['overview', 'step buttons', 'real keyword search', 'semantic filter guard', 'local report', 'model masking', 'tasks', 'mobile layout']}, ensure_ascii=False))
    if errors: raise SystemExit(1)
