#!/usr/bin/env python3
import argparse
import hashlib
import json
import pathlib
import shutil
import time

from playwright.sync_api import sync_playwright


def wait_for_worker(context):
    for _ in range(120):
        if context.service_workers:
            return context.service_workers[0]
        time.sleep(0.5)
    return context.wait_for_event("serviceworker", timeout=60_000)


def launch(playwright, profile, extension, width, height, color_scheme="light", reduced_motion="no-preference"):
    context = playwright.chromium.launch_persistent_context(
        str(profile),
        headless=False,
        args=[
            f"--disable-extensions-except={extension}",
            f"--load-extension={extension}",
            f"--window-size={width},{height}",
        ],
        viewport={"width": width, "height": height},
        color_scheme=color_scheme,
        reduced_motion=reduced_motion,
    )
    context.on("page", lambda page: page.on(
        "console", lambda message: print(f"[console:{message.type}] {message.text}", flush=True)
    ))
    context.on("page", lambda page: page.on(
        "pageerror", lambda error: print(f"[pageerror] {error}", flush=True)
    ))
    worker = wait_for_worker(context)
    return context, worker.url.split("/")[2]


def inspect(page):
    return page.evaluate(
        """() => {
          const role = (name) => document.querySelector(`[data-sc-bee-role="${name}"]`);
          const chart = role('reading-donut');
          const pie = role('reading-pie');
          const legend = role('donut-legend');
          const image = pie ? pie.querySelector('canvas.gloss-image, img.gloss-image') : null;
          const imageLink = image ? image.closest('.gloss-image-link') : null;
          const details = document.querySelector('details');
          const rect = (node) => node ? node.getBoundingClientRect().toJSON() : null;
          const chartRect = chart ? chart.getBoundingClientRect() : null;
          const pieRect = pie ? pie.getBoundingClientRect() : null;
          const legendRect = legend ? legend.getBoundingClientRect() : null;
          let canvasPixels = null;
          if (image && image.tagName === 'CANVAS') {
            try {
              const pixels = image.getContext('2d').getImageData(0, 0, image.width, image.height).data;
              let nonTransparent = 0;
              let nonBlack = 0;
              for (let i = 0; i < pixels.length; i += 4) {
                if (pixels[i + 3] !== 0) nonTransparent += 1;
                if (pixels[i] !== 0 || pixels[i + 1] !== 0 || pixels[i + 2] !== 0) nonBlack += 1;
              }
              canvasPixels = {nonTransparent, nonBlack};
            } catch (error) {
              canvasPixels = {transferredToOffscreen: true};
            }
          }
          const verticalOverlap = pieRect && legendRect
            ? Math.max(0, Math.min(pieRect.bottom, legendRect.bottom) - Math.max(pieRect.top, legendRect.top))
            : 0;
          return {
            viewport: {width: innerWidth, height: innerHeight},
            entryTypes: Array.from(document.querySelectorAll('.entry')).map(e => e.dataset.type || null),
            rich: {
              hero: !!role('hero'),
              readingChips: document.querySelectorAll('[data-sc-bee-role="reading-chip"]').length,
              badgeRow: !!role('badge-row'),
              frequencyChart: !!chart,
              vocabGroups: document.querySelectorAll('[data-sc-bee-role="vocab-group"]').length,
              learningAids: !!details,
            },
            frequencyChart: chart ? {
              rect: rect(chart),
              pieRect: rect(pie),
              legendRect: rect(legend),
              imageRect: rect(image),
              text: chart.innerText,
              titlePresent: document.body.innerText.toLowerCase().includes('frequency weight'),
              wordVarietyPresent: document.body.innerText.toLowerCase().includes('word variety'),
              pieLeftOfLegend: !!(pieRect && legendRect && pieRect.right <= legendRect.left + 1),
              sideBySide: verticalOverlap > 1,
              overlap: !!(pieRect && legendRect && pieRect.left < legendRect.right && pieRect.right > legendRect.left && pieRect.top < legendRect.bottom && pieRect.bottom > legendRect.top),
              ownOverflow: chart.scrollWidth > chart.clientWidth,
            } : null,
            detailsOpen: details ? details.open : null,
            frequencyImage: image ? {
              rect: rect(image),
              tagName: image.tagName,
              width: image.width,
              height: image.height,
              loadState: imageLink ? imageLink.dataset.imageLoadState : null,
              canvasPixels,
            } : null,
            bodyText: document.body.innerText,
            horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
          };
        }"""
    )


def search_character(page, extension_id, character):
    page.goto(f"chrome-extension://{extension_id}/search.html")
    page.wait_for_load_state("load")
    box = page.locator("#search-textbox")
    box.fill(character)
    box.press("Enter")
    page.wait_for_selector('[data-sc-bee-role="hero"]', timeout=30_000)
    time.sleep(1)


def expand(page):
    summary = page.locator("details summary").first
    if summary.count():
        summary.click()
        page.wait_for_timeout(1_000)


def capture_stroke(page, path):
    image = page.locator("details canvas.gloss-image, details img.gloss-image").first
    if image.count():
        image.scroll_into_view_if_needed()
        page.wait_for_timeout(5_000)
        page.screenshot(path=str(path))


def assert_frequency_chart(label, state, *, narrow=False):
    chart = state.get("frequencyChart")
    image = state.get("frequencyImage")
    if not chart or not chart["titlePresent"]:
        raise RuntimeError(f"{label}: Frequency weight chart is missing")
    if chart["wordVarietyPresent"]:
        raise RuntimeError(f"{label}: rejected Word variety comparison is visible")
    if chart["overlap"] or chart["ownOverflow"] or state["horizontalOverflow"]:
        raise RuntimeError(f"{label}: chart overlap or horizontal overflow detected")
    if not chart["pieLeftOfLegend"] or not chart["sideBySide"]:
        raise RuntimeError(f"{label}: pie is not left beside its legend")
    if not image or image["tagName"] != "CANVAS":
        raise RuntimeError(f"{label}: Yomitan did not render the packaged PNG to canvas")
    pixels = image.get("canvasPixels") or {}
    if not pixels.get("nonTransparent") and not pixels.get("transferredToOffscreen"):
        raise RuntimeError(f"{label}: rendered chart canvas has no visible pixels")
    if narrow and state["viewport"]["width"] != 380:
        raise RuntimeError(f"{label}: narrow acceptance did not run at 380 px")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("zip", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    parser.add_argument("--profile", type=pathlib.Path, required=True)
    parser.add_argument("--extension", type=pathlib.Path, default=pathlib.Path("/tmp/repro/ext"))
    parser.add_argument("--reuse", action="store_true")
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    if args.profile.exists() and not args.reuse:
        shutil.rmtree(args.profile)
    zip_bytes = args.zip.read_bytes()
    results = {
        "zip": str(args.zip.resolve()),
        "zipSha256": hashlib.sha256(zip_bytes).hexdigest(),
        "zipBytes": len(zip_bytes),
        "profile": str(args.profile.resolve()),
    }

    with sync_playwright() as playwright:
        context, extension_id = launch(playwright, args.profile, args.extension, 1280, 900)
        page = context.new_page()
        page.goto(f"chrome-extension://{extension_id}/settings.html")
        page.wait_for_load_state("load")
        page.wait_for_timeout(2_000)
        page.keyboard.press("Escape")
        import_started = None
        if not args.reuse:
            file_input = page.locator("#dictionary-import-file-input")
            if file_input.count() != 1:
                raise RuntimeError("Yomitan dictionary import input was not found")
            import_started = time.time()
            file_input.set_input_files(str(args.zip.resolve()))
            deadline = time.time() + 900
            last_progress_log = 0.0
            while time.time() < deadline:
                settings_text = page.locator("body").inner_text()
                import_dialog = page.locator('.modal-container:not(.hidden), [role="dialog"]:visible').count()
                if (
                    "Bee's Ultimate Kanji Dictionary" in settings_text
                    and "1 installed" in settings_text
                    and import_dialog == 0
                ):
                    break
                if time.time() - last_progress_log >= 30:
                    print("[import] waiting for completed installed dictionary", flush=True)
                    last_progress_log = time.time()
                time.sleep(2)
            else:
                raise RuntimeError("dictionary import did not finish within 15 minutes")
        elif "Bee's Ultimate Kanji Dictionary" not in page.locator("body").inner_text():
            raise RuntimeError("reused profile does not contain the dictionary")
        page.wait_for_timeout(3_000)
        results["import"] = {
            "ok": True,
            "seconds": round(time.time() - import_started, 3) if import_started is not None else None,
            "extensionId": extension_id,
            "settingsText": page.locator("body").inner_text(),
        }
        page.screenshot(path=str(args.output / "00-settings-clean-import.png"), full_page=True)

        search = context.new_page()
        results["characters"] = {}
        for character in ("生", "場"):
            search_character(search, extension_id, character)
            compact = inspect(search)
            print(json.dumps({"character": character, "compact": compact}, ensure_ascii=False, indent=2), flush=True)
            assert_frequency_chart(f"{character} compact", compact)
            search.screenshot(path=str(args.output / f"{character}-compact.png"), full_page=True)
            link = search.locator("a.headword-kanji-link").first
            results["characters"][character] = {"compact": compact, "hasKanjiLink": bool(link.count())}
            expand(search)
            expanded = inspect(search)
            search.screenshot(path=str(args.output / f"{character}-expanded.png"), full_page=True)
            capture_stroke(search, args.output / f"{character}-stroke-visible.png")
            results["characters"][character]["expanded"] = expanded
            if link.count():
                link.click()
                search.wait_for_timeout(1_500)
                results["characters"][character]["kanjiDrilldownText"] = search.locator("body").inner_text()
                search.screenshot(path=str(args.output / f"{character}-native-drilldown.png"), full_page=True)
        # Keep the freshly imported extension session alive for responsive/theme
        # checks. Relaunching Chromium against an unpacked extension can race its
        # media drawing worker and produce a false blank-canvas result.
        search.set_viewport_size({"width": 380, "height": 820})
        search_character(search, extension_id, "場")
        expand(search)
        results["narrow"] = inspect(search)
        assert_frequency_chart("場 narrow", results["narrow"], narrow=True)
        search.screenshot(path=str(args.output / "場-narrow-expanded.png"), full_page=True)
        capture_stroke(search, args.output / "場-narrow-stroke-visible.png")

        search.set_viewport_size({"width": 380, "height": 820})
        search.emulate_media(color_scheme="dark", reduced_motion="reduce")
        search_character(search, extension_id, "場")
        expand(search)
        results["dark"] = inspect(search)
        assert_frequency_chart("場 dark narrow", results["dark"], narrow=True)
        search.screenshot(path=str(args.output / "場-dark-narrow-expanded.png"), full_page=True)
        capture_stroke(search, args.output / "場-dark-narrow-stroke-visible.png")

        search.set_viewport_size({"width": 380, "height": 820})
        search_character(search, extension_id, "生")
        expand(search)
        results["reducedMotion"] = inspect(search)
        assert_frequency_chart("生 dark reduced-motion narrow", results["reducedMotion"], narrow=True)
        search.screenshot(path=str(args.output / "生-dark-reduced-motion-expanded.png"), full_page=True)
        capture_stroke(search, args.output / "生-dark-reduced-motion-stroke-visible.png")
        context.close()

    (args.output / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
